#!/usr/bin/env python3
"""
Real browser smoke test for the share-audit dashboard.

Starts the local dashboard, opens it in headless Chrome via the Chrome DevTools
Protocol, clicks through tier and peer flows, and saves desktop/mobile PNGs.

Run: .venv/bin/python test_share_audit_ui_browser.py
"""

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import agents                 # noqa: E402
import app_layer              # noqa: E402
import share_audit_server     # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed():
    work = tempfile.mkdtemp(prefix="linkedout_ui_browser_")
    share = os.path.join(work, "share")
    app_layer.ensure_share(share)
    files = {
        ("read-only", "profile.md"): "PUBLIC: Alice likes Snow Crash.\n",
        ("read&append", "inbox.md"): "APPENDABLE: peer notes land here.\n",
        ("task", "demo.md"): "TASK: Scenario D review checklist.\n",
        ("personal", "diary.md"): "SECRET: personal-only material should stay hidden.\n",
    }
    for (zone, name), body in files.items():
        with open(os.path.join(share, zone, name), "w", encoding="utf-8") as f:
            f.write(body)
    agents_file = os.path.join(work, "agents.json")
    pub = "c" * 64
    agents.add(pub, "Carol", agents_file, tier="task")
    agents.add("d" * 64, "Dave", agents_file, tier="personal")
    return work, share, agents_file, pub


def _start_dashboard(share, agents_file):
    server = share_audit_server.make_server("127.0.0.1", 0, share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _start_chrome(port, profile):
    if not os.path.exists(CHROME):
        raise RuntimeError(f"Chrome not found at {CHROME}")
    return subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--disable-gpu",
            "--no-first-run",
            "about:blank",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for_cdp(port, timeout=10):
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as res:
                return json.loads(res.read().decode("utf-8"))
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("Chrome DevTools endpoint did not become ready")


class CDP:
    def __init__(self, websocket_url):
        parsed = urllib.parse.urlparse(websocket_url)
        self.host = parsed.hostname
        self.port = parsed.port
        self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self.next_id = 0
        self._handshake()

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode("ascii"))
        data = b""
        while b"\r\n\r\n" not in data:
            data += self.sock.recv(4096)
        if b" 101 " not in data.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket handshake failed: {data[:120]!r}")
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        )
        if accept not in data:
            raise RuntimeError("WebSocket accept header mismatch")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _send_frame(self, text):
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", n))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def _recv_exact(self, n):
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("WebSocket closed")
            out += chunk
        return out

    def _recv_frame(self):
        first = self._recv_exact(2)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 8:
            raise RuntimeError("WebSocket close frame")
        if opcode == 9:
            return self._recv_frame()
        return payload.decode("utf-8")

    def send(self, method, params=None):
        self.next_id += 1
        ident = self.next_id
        self._send_frame(json.dumps({"id": ident, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self._recv_frame())
            if msg.get("id") != ident:
                continue
            if "error" in msg:
                raise RuntimeError(json.dumps(msg["error"]))
            return msg.get("result", {})

    def eval(self, expression):
        result = self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        if "exceptionDetails" in result:
            raise RuntimeError(json.dumps(result["exceptionDetails"]))
        return result.get("result", {}).get("value")


def _new_tab(port, url):
    target = urllib.parse.quote(url, safe=":/?=&%")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{target}", method="PUT")
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.loads(res.read().decode("utf-8"))


def _wait_status(cdp, expected="Audit complete", timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = cdp.eval("document.querySelector('#status')?.textContent || ''")
        if state == expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"status did not become {expected!r}")


def _page_state(cdp):
    return cdp.eval(
        """(() => ({
          status: document.querySelector('#status')?.textContent,
          mode: document.querySelector('#mode')?.value,
          selectedPeer: document.querySelector('#peer')?.selectedOptions?.[0]?.textContent || '',
          tier: document.querySelector('#tierBadge')?.textContent,
          visibleZones: document.querySelector('#visibleZones')?.textContent,
          entries: document.querySelector('#entryCount')?.textContent,
          chunks: document.querySelector('#chunkCount')?.textContent,
          appendable: document.querySelector('#appendZones')?.textContent,
          selfPanelText: document.querySelector('[data-testid="self-audit-panel"]')?.innerText || '',
          selfHost: document.querySelector('#selfHost')?.textContent,
          selfFriendCount: document.querySelector('#selfFriendCount')?.textContent,
          importZoneCount: document.querySelectorAll('#importZones .import-zone').length,
          hasProfile: document.body.innerText.includes('read-only/profile.md'),
          hasTask: document.body.innerText.includes('task/demo.md'),
          hasPersonalPath: document.body.innerText.includes('personal/diary.md'),
          hasSecretContent: document.body.innerText.includes('SECRET:'),
          previewTier: document.querySelector('#previewTier')?.textContent,
          previewEntries: document.querySelector('#previewEntries')?.textContent,
          previewHasPersonal: document.querySelector('#previewList')?.innerText.includes('personal/diary.md') || false,
          matrixState: document.querySelector('#matrixState')?.textContent,
          matrixRowCount: document.querySelectorAll('#matrixRows tr').length,
          matrixText: document.querySelector('#matrixRows')?.innerText || '',
          matrixPersonalRows: document.querySelectorAll('.matrix-row-personal').length,
          matrixHasPersonalPath: document.querySelector('#matrixRows')?.innerText.includes('personal/diary.md') || false,
          matrixHasUndefined: document.querySelector('#matrixRows')?.innerText.includes('undefined') || false,
          matrixPanelOverflowX: (() => {
            const body = document.querySelector('[data-testid="peer-matrix-panel"] .panel-body');
            return body ? body.scrollWidth > body.clientWidth : false;
          })(),
          width: document.documentElement.clientWidth,
          overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth
        }))()"""
    )


def _screenshot(cdp, path):
    shot = cdp.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    with open(path, "wb") as f:
        f.write(base64.b64decode(shot["data"]))


def main():
    work, share, agents_file, _ = _seed()
    server = _start_dashboard(share, agents_file)
    dashboard_port = server.server_address[1]
    chrome_port = _free_port()
    chrome = None
    cdp = None
    try:
        chrome = _start_chrome(chrome_port, os.path.join(work, "chrome-profile"))
        _wait_for_cdp(chrome_port)
        url = f"http://127.0.0.1:{dashboard_port}/?{urllib.parse.urlencode({'share': share, 'agents_file': agents_file, 'tier': 'common'})}"
        target = _new_tab(chrome_port, url)
        cdp = CDP(target["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride", {
            "width": 1365, "height": 820, "deviceScaleFactor": 1, "mobile": False,
        })
        _wait_status(cdp)

        common = _page_state(cdp)
        assert common["selfHost"] == "this computer", common
        assert common["selfFriendCount"] == "2", common
        assert common["importZoneCount"] == 4, common
        assert "local-only" in common["selfPanelText"], common
        assert "read-only/" in common["selfPanelText"], common
        assert "personal/" in common["selfPanelText"], common
        self_png = os.path.join(work, "share-audit-self.png")
        _screenshot(cdp, self_png)
        assert common["visibleZones"] == "2", common
        assert common["hasProfile"] is True, common
        assert common["hasTask"] is False, common
        assert common["hasPersonalPath"] is False, common
        assert common["hasSecretContent"] is False, common
        assert common["matrixState"] == "2 peers", common
        assert common["matrixRowCount"] == 2, common
        assert "Carol" in common["matrixText"], common
        assert "Dave" in common["matrixText"], common
        assert common["matrixPersonalRows"] == 1, common
        assert common["matrixHasPersonalPath"] is False, common
        assert common["matrixHasUndefined"] is False, common
        cdp.eval("document.querySelector('[data-matrix-filter=\"personal\"]').click();")
        filtered = _page_state(cdp)
        assert filtered["matrixState"] == "1/2 peers", filtered
        assert filtered["matrixRowCount"] == 1, filtered
        assert "Dave" in filtered["matrixText"], filtered
        assert "Carol" not in filtered["matrixText"], filtered
        cdp.eval("document.querySelector('[data-matrix-filter=\"all\"]').click();")
        all_rows = _page_state(cdp)
        assert all_rows["matrixState"] == "2 peers", all_rows
        cdp.eval("document.querySelector('[data-testid=\"peer-matrix-panel\"]').scrollIntoView({block: 'start'});")
        time.sleep(0.2)
        matrix_png = os.path.join(work, "share-audit-matrix.png")
        _screenshot(cdp, matrix_png)

        cdp.eval("document.querySelector('[data-tier=\"task\"]').click(); document.querySelector('#runAudit').click();")
        _wait_status(cdp)
        task = _page_state(cdp)
        assert task["visibleZones"] == "3", task
        assert task["hasTask"] is True, task
        assert task["hasPersonalPath"] is False, task
        assert task["hasSecretContent"] is False, task
        desktop_png = os.path.join(work, "share-audit-desktop.png")
        _screenshot(cdp, desktop_png)

        cdp.eval("document.querySelector('[data-matrix-action=\"inspect\"][data-row-index=\"0\"]').click();")
        _wait_status(cdp, "Peer audit complete")
        inspected = _page_state(cdp)
        assert inspected["mode"] == "peer", inspected
        assert inspected["selectedPeer"].startswith("Carol"), inspected
        assert inspected["tier"] == "tier: task", inspected
        assert inspected["hasTask"] is True, inspected
        assert inspected["hasPersonalPath"] is False, inspected
        assert inspected["hasSecretContent"] is False, inspected

        cdp.eval("document.querySelector('[data-matrix-action=\"preview\"][data-row-index=\"0\"]').click();")
        _wait_status(cdp, "Preview ready")
        action_preview = _page_state(cdp)
        assert action_preview["mode"] == "peer", action_preview
        assert action_preview["selectedPeer"].startswith("Carol"), action_preview
        assert action_preview["previewTier"] == "task → personal", action_preview
        assert action_preview["previewHasPersonal"] is True, action_preview

        cdp.eval("document.querySelector('#mode').value = 'peer'; document.querySelector('#mode').dispatchEvent(new Event('change')); document.querySelector('#loadPeers').click();")
        deadline = time.time() + 8
        while time.time() < deadline:
            if cdp.eval("document.querySelector('#status')?.textContent") == "Loaded 2 peers":
                break
            time.sleep(0.1)
        peers = cdp.eval("[...document.querySelector('#peer').options].map(o => o.textContent).join('|')")
        assert peers.startswith("Carol"), peers
        assert "Dave" in peers, peers
        cdp.eval("document.querySelector('#runAudit').click();")
        _wait_status(cdp)
        peer = _page_state(cdp)
        assert peer["tier"] == "tier: task", peer
        assert peer["hasTask"] is True, peer
        assert peer["previewTier"] == "task → personal", peer
        assert peer["previewEntries"] == "2", peer
        assert peer["previewHasPersonal"] is True, peer
        assert peer["hasPersonalPath"] is True, peer
        assert peer["hasSecretContent"] is False, peer
        cdp.eval("document.querySelector('#previewPanel').scrollIntoView({block: 'start'});")
        time.sleep(0.2)
        preview_png = os.path.join(work, "share-audit-preview.png")
        _screenshot(cdp, preview_png)

        cdp.eval("document.querySelector('[data-matrix-filter=\"personal\"]').click(); document.querySelector('[data-matrix-action=\"preview\"][data-row-index=\"0\"]').click();")
        _wait_status(cdp, "Preview ready")
        filtered_action = _page_state(cdp)
        assert filtered_action["selectedPeer"].startswith("Dave"), filtered_action
        assert filtered_action["tier"] == "tier: personal", filtered_action
        assert filtered_action["previewTier"] == "personal → personal", filtered_action
        assert filtered_action["hasSecretContent"] is False, filtered_action

        cdp.send("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True,
        })
        time.sleep(0.4)
        mobile = _page_state(cdp)
        assert mobile["overflowX"] is False, mobile
        assert mobile["matrixPanelOverflowX"] is False, mobile
        mobile_png = os.path.join(work, "share-audit-mobile.png")
        _screenshot(cdp, mobile_png)

        print("✅ browser common tier hides task/personal")
        print("✅ browser task tier reveals task but not personal")
        print("✅ browser matrix inspect action selects a peer and runs audit")
        print("✅ browser matrix preview action opens the trust-change preview")
        print("✅ browser matrix filters focus personal peers and preserve row actions")
        print("✅ browser peer flow loads Carol from agents.json")
        print("✅ browser peer matrix lists all peers and highlights personal tier")
        print("✅ browser trust preview shows newly exposed personal paths without contents")
        print("✅ mobile viewport has no horizontal overflow")
        print(f"SELF_SCREENSHOT={self_png}")
        print(f"MATRIX_SCREENSHOT={matrix_png}")
        print(f"DESKTOP_SCREENSHOT={desktop_png}")
        print(f"PREVIEW_SCREENSHOT={preview_png}")
        print(f"MOBILE_SCREENSHOT={mobile_png}")
        print("\n🎉 share-audit browser UI test passed")
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


if __name__ == "__main__":
    main()
