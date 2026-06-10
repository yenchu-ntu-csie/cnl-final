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
            "這版 demo 不是功能巡覽，而是 Bob 的五個實際決策問題。\n每張卡都回答：問題是什麼、證據在哪裡、最後該怎麼決定。",
            6.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "my-computer")
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "問題 1：Bob 能不能安全地用這台電腦 demo？\n證據：self-audit 顯示 audit 跑在本機、share zones 和 common-tier exposure。決策：不要依賴中央伺服器。",
            7.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "who-trust")
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "問題 2：Bob 目前到底信任誰？\n證據：matrix 只讀 Bob 本機 agents.json，列出每個 peer 的 tier 和可見 zones。決策：只有這裡出現的人是直接 trust decision。",
            7.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "who-can-write")
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "問題 3：誰能在 live demo 時寫進 Bob 的 inbox？\n證據：Appendable filter 把可寫入 read&append/ 的 peer 挑出來。決策：先審這些人，再開放協作。",
            7.0,
            fps,
            captions,
        )

        _run_scenario(cdp, "inspect-carol")
        frame_no = _caption_hold(
            cdp,
            frames_dir,
            frame_no,
            "問題 4：Carol 能加入 project，但不看到 personal notes 嗎？\n證據：inspect Carol 後可以看到 task/，但 personal/ 仍然隱藏。決策：Carol 適合 project collaboration。",
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
            "問題 5：Bob 該不該把 Carol 升到 personal？\n證據：preview 在改 agents.json 前列出新曝光 paths 和 ask chunk delta。決策：除非這些 personal paths 是預期的，否則不要升級。",
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
