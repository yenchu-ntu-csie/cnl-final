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
              'right: 18px',
              'bottom: 18px',
              'z-index: 9999',
              'max-width: 520px',
              'padding: 10px 14px',
              'border-radius: 8px',
              'background: rgba(18, 26, 36, 0.88)',
              'color: #fff',
              'font: 600 14px/1.35 system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
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


def _scroll_to(cdp, selector, label, frames_dir, frame_no, seconds=1.2, fps=8):
    _set_scene_label(cdp, label)
    cdp.eval(f"document.querySelector({json.dumps(selector)})?.scrollIntoView({{block: 'start'}});")
    time.sleep(0.25)
    return _capture_hold(cdp, frames_dir, frame_no, seconds, fps)


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


def record(output, fps=8, keep_frames=False):
    work, share, agents_file, _ = _seed()
    frames_dir = os.path.join(work, "frames")
    os.makedirs(frames_dir, exist_ok=True)
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

        _set_scene_label(cdp, "Start with Bob's scenario cards")
        frame_no = _capture_hold(cdp, frames_dir, frame_no, 1.6, fps)

        _run_scenario(cdp, "my-computer")
        _set_scene_label(cdp, "Scenario 1: Bob audits this computer, not a central server")
        frame_no = _capture_hold(cdp, frames_dir, frame_no, 1.8, fps)

        _run_scenario(cdp, "who-trust")
        _set_scene_label(cdp, "Scenario 2: Bob sees who is in local agents.json")
        frame_no = _capture_hold(cdp, frames_dir, frame_no, 1.8, fps)

        _run_scenario(cdp, "who-can-write")
        _set_scene_label(cdp, "Scenario 3: Bob filters peers who can write into read&append/")
        frame_no = _capture_hold(cdp, frames_dir, frame_no, 1.8, fps)

        _run_scenario(cdp, "inspect-carol")
        _set_scene_label(cdp, "Scenario 4: inspect a project friend right now")
        frame_no = _capture_hold(cdp, frames_dir, frame_no, 1.8, fps)

        _run_scenario(cdp, "upgrade-trust")
        _page_state(cdp)
        _set_scene_label(cdp, "Scenario 5: preview newly exposed paths before changing trust")
        frame_no = _capture_hold(cdp, frames_dir, frame_no, 2.2, fps)

        _encode_video(frames_dir, output, fps)
        if keep_frames:
            print(f"FRAMES_DIR={frames_dir}")
        else:
            shutil.rmtree(frames_dir, ignore_errors=True)
        print(f"DEMO_VIDEO={output}")
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
    parser.add_argument("--keep-frames", action="store_true")
    args = parser.parse_args()
    record(os.path.abspath(args.output), fps=args.fps, keep_frames=args.keep_frames)


if __name__ == "__main__":
    main()
