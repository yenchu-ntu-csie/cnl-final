#!/usr/bin/env python3
"""
Record a browser-driven demo video for the LinkedOut share audit dashboard.

The recorder starts the local dashboard, drives headless Chrome through the
same real UI flows as the browser smoke test, captures PNG frames, and encodes
them into an mp4 with ffmpeg.

Run:
    .venv/bin/python -B script/record_share_audit_demo.py
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_share_audit_ui_browser import (  # noqa: E402
    CDP,
    _free_port,
    _new_tab,
    _page_state,
    _screenshot,
    _seed,
    _start_chrome,
    _start_dashboard,
    _wait_for_cdp,
    _wait_status,
)


def _set_scene_label(cdp, label):
    cdp.eval(
        f"""(() => {{
          let el = document.querySelector('#demoSceneLabel');
          if (!el) {{
            el = document.createElement('div');
            el.id = 'demoSceneLabel';
            el.style.cssText = [
              'position: fixed',
              'left: 50%',
              'bottom: 22px',
              'transform: translateX(-50%)',
              'z-index: 9999',
              'width: min(940px, calc(100vw - 40px))',
              'padding: 12px 18px',
              'border-radius: 8px',
              'background: rgba(18, 26, 36, 0.90)',
              'color: #fff',
              'font: 700 22px/1.45 system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
              'text-align: center',
              'white-space: pre-line',
              'box-shadow: 0 12px 34px rgba(15, 23, 42, 0.24)'
            ].join(';');
            document.body.appendChild(el);
          }}
          el.textContent = {json.dumps(label)};
        }})()"""
    )


def _capture_hold(cdp, frames_dir, frame_no, seconds=1.5, fps=8):
    frames = max(1, int(seconds * fps))
    delay = 1.0 / fps
    for _ in range(frames):
        frame_no += 1
        _screenshot(cdp, os.path.join(frames_dir, f"frame-{frame_no:04d}.png"))
        time.sleep(delay)
    return frame_no


def _format_srt_time(seconds):
    millis = round(seconds * 1000)
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _write_srt(path, captions):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for index, item in enumerate(captions, start=1):
            f.write(f"{index}\n")
            f.write(f"{_format_srt_time(item['start'])} --> {_format_srt_time(item['end'])}\n")
            f.write(item["text"])
            if index != len(captions):
                f.write("\n\n")


def _caption_hold(cdp, frames_dir, frame_no, caption, seconds, fps, captions):
    start = frame_no / fps
    _set_scene_label(cdp, caption)
    frame_no = _capture_hold(cdp, frames_dir, frame_no, seconds, fps)
    captions.append({"start": start, "end": frame_no / fps, "text": caption})
    return frame_no


def _run_scenario(cdp, scenario_id):
    cdp.eval(f"document.querySelector('[data-scenario-run=\"{scenario_id}\"]').click();")
    _wait_status(cdp, "Scenario ready")


def _encode_video(frames_dir, output, fps):
    os.makedirs(os.path.dirname(output), exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        os.path.join(frames_dir, "frame-%04d.png"),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-movflags",
        "+faststart",
        output,
    ]
    subprocess.run(cmd, check=True)


def record(output, fps=8, keep_frames=False, srt_output=None):
    work, share, agents_file, _ = _seed()
    frames_dir = os.path.join(work, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    captions = []
    srt_output = srt_output or os.path.splitext(output)[0] + ".zh.srt"
    server = _start_dashboard(share, agents_file)
    dashboard_port = server.server_address[1]
    chrome_port = _free_port()
    chrome = None
    cdp = None
    frame_no = 0
    try:
        chrome = _start_chrome(chrome_port, os.path.join(work, "chrome-profile"))
        _wait_for_cdp(chrome_port)
        query = urllib.parse.urlencode({
            "share": share,
            "agents_file": agents_file,
            "tier": "common",
        })
        target = _new_tab(chrome_port, f"http://127.0.0.1:{dashboard_port}/?{query}")
        cdp = CDP(target["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride", {
            "width": 1366,
            "height": 768,
            "deviceScaleFactor": 1,
            "mobile": False,
        })
        _wait_status(cdp)

        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "這版 demo 從提問開始，不是只看 audit log。\n畫面上方的 Ask Composer 會顯示 Bob 的問題、target、context scope、excluded zones。",
            6.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "my-computer")
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "具體問句 1：如果我從這台 laptop demo，common peer 到底能看到什麼？\nAsk plan：target 是 explicit common；context 只有 common zones，不是中央伺服器判斷。",
            7.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "who-trust")
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "具體問句 2：我的 agents.json 裡到底有哪些朋友、各是什麼 tier？\nAsk plan + matrix：列出 Carol、Dave 的 tier、zones、ask chunks，但不顯示筆記內容。",
            7.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "who-can-write")
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "具體問句 3：live demo 時，誰可以寫進我的 read&append/ inbox？\nAsk Composer 保留問題；Appendable filter 只留下有寫入權的 peer，讓 Bob 先審寫入風險。",
            7.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "inspect-carol")
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "具體問句 4：Carol 可以幫 project，但不看到 personal notes 嗎？\nAsk plan 會標成 constrained：問題提到 personal/，但 Carol 的 task tier 不會帶入 personal context。",
            7.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "upgrade-trust")
        _page_state(cdp)
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "具體問句 5：如果把 Carol 從 task 升到 personal，會多曝光什麼？\nPreview 先列新 paths 和 ask chunk delta；真正送出 ask 前先看這個差異。",
            8.0,
            fps,
            captions,
        )

        _encode_video(frames_dir, output, fps)
        _write_srt(srt_output, captions)
        if keep_frames:
            print(f"FRAMES_DIR={frames_dir}")
        else:
            shutil.rmtree(frames_dir, ignore_errors=True)
        print(f"DEMO_VIDEO={output}")
        print(f"SUBTITLES={srt_output}")
        print(f"FRAME_COUNT={frame_no}")
        print("OK")
        return output
    finally:
        if cdp:
            cdp.close()
        if chrome:
            chrome.terminate()
            try:
                chrome.wait(timeout=3)
            except subprocess.TimeoutExpired:
                chrome.kill()
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=os.path.join(ROOT, "demo", "artifacts", "share-audit-demo.mp4"),
        help="Path to the mp4 demo video to create.",
    )
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument(
        "--srt-output",
        default=None,
        help="Path to a Chinese .srt sidecar. Defaults to output basename + .zh.srt.",
    )
    parser.add_argument("--keep-frames", action="store_true")
    args = parser.parse_args()
    record(
        os.path.abspath(args.output),
        fps=args.fps,
        keep_frames=args.keep_frames,
        srt_output=os.path.abspath(args.srt_output) if args.srt_output else None,
    )


if __name__ == "__main__":
    main()
