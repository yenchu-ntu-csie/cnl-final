#!/usr/bin/env python3
"""
Share-audit web UI API tests. No browser, network peer, or Ollama required.

Run: .venv/bin/python test_share_audit_server.py
"""

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import agents              # noqa: E402
import app_layer           # noqa: E402
import share_audit_server  # noqa: E402


def _seed():
    work = tempfile.mkdtemp()
    share = os.path.join(work, "share")
    app_layer.ensure_share(share)
    with open(os.path.join(share, "read-only", "public.md"), "w", encoding="utf-8") as f:
        f.write("PUBLIC alpha")
    with open(os.path.join(share, "task", "task.md"), "w", encoding="utf-8") as f:
        f.write("TASK beta")
    with open(os.path.join(share, "personal", "secret.md"), "w", encoding="utf-8") as f:
        f.write("SECRET gamma")
    agents_file = os.path.join(work, "agents.json")
    pub = "b" * 64
    agents.add(pub, "Bob", agents_file, tier="task")
    return work, share, agents_file, pub


def _get_json(base, path, **query):
    url = f"{base}{path}?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url, timeout=5) as res:
        return json.loads(res.read().decode("utf-8"))


def _get_response(base, path="/", headers=None):
    req = urllib.request.Request(f"{base}{path}", headers=headers or {})
    return urllib.request.urlopen(req, timeout=5)


def test_api_serves_audit_and_agents():
    _, share, agents_file, pub = _seed()
    server = share_audit_server.make_server("127.0.0.1", 0, share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        peers = _get_json(base, "/api/agents", agents_file=agents_file)
        assert peers["ok"] is True, peers
        assert peers["peers"][0]["name"] == "Bob", peers
        audit = _get_json(base, "/api/audit", share=share, agents_file=agents_file, peer_pubkey=pub)
        paths = {entry["path"] for entry in audit["audit"]["list"]["entries"]}
        rendered = json.dumps(audit, ensure_ascii=False)
        assert "read-only/public.md" in paths, paths
        assert "task/task.md" in paths, paths
        assert "personal/secret.md" not in paths, paths
        assert "PUBLIC alpha" not in rendered, rendered
        assert "TASK beta" not in rendered, rendered
        print("✅ audit UI API serves peer audit without file contents")
    finally:
        server.shutdown()
        server.server_close()


def test_api_rejects_missing_subject():
    _, share, agents_file, _ = _seed()
    server = share_audit_server.make_server("127.0.0.1", 0, share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        try:
            _get_json(base, "/api/audit", share=share, agents_file=agents_file)
            raise AssertionError("missing tier/peer should fail")
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            assert e.code == 400, e.code
            assert body["ok"] is False, body
        print("✅ audit UI API rejects missing tier/peer")
    finally:
        server.shutdown()
        server.server_close()


def test_api_previews_trust_change_without_contents():
    _, share, agents_file, pub = _seed()
    server = share_audit_server.make_server("127.0.0.1", 0, share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        preview = _get_json(
            base,
            "/api/preview-tier-change",
            share=share,
            agents_file=agents_file,
            peer_pubkey=pub,
            proposed_tier="personal",
        )
        rendered = json.dumps(preview, ensure_ascii=False)
        exposed = preview["preview"]["newly_exposed"]
        new_paths = {entry["path"] for entry in exposed["entries"]}
        assert preview["preview"]["current_tier"] == "task", preview
        assert preview["preview"]["proposed_tier"] == "personal", preview
        assert exposed["zones"] == ["personal"], preview
        assert "personal/secret.md" in new_paths, preview
        assert preview["preview"]["ask_context"]["delta"]["chunks"] == 1, preview
        assert preview["preview"]["ask_context"]["delta"]["bytes"] > 0, preview
        assert preview["preview"]["ask_context"]["content_included"] is False, preview
        assert "current" not in preview, preview
        assert "proposed" not in preview, preview
        assert "diff" not in preview, preview
        assert "SECRET gamma" not in rendered, rendered
        assert "TASK beta" not in rendered, rendered
        print("✅ audit UI API previews tier changes without file contents")
    finally:
        server.shutdown()
        server.server_close()


def test_html_is_safe_and_browser_testable():
    _, _, agents_file, _ = _seed()
    unsafe_share = 'share" autofocus onfocus="alert(1)'
    server = share_audit_server.make_server("127.0.0.1", 0, unsafe_share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with _get_response(base) as res:
            body = res.read().decode("utf-8")
            csp = res.headers.get("Content-Security-Policy", "")
            assert "frame-ancestors 'none'" in csp, csp
            assert res.headers.get("X-Content-Type-Options") == "nosniff"
            assert 'share&quot; autofocus onfocus=&quot;alert(1)' in body, body
            assert 'value="share" autofocus' not in body, body
            assert 'data-testid="run-audit"' in body, body
            assert "new URLSearchParams(window.location.search)" in body, body
        print("✅ audit UI HTML escapes defaults and exposes stable browser hooks")
    finally:
        server.shutdown()
        server.server_close()


def test_rejects_dns_rebinding_host_header():
    _, share, agents_file, _ = _seed()
    server = share_audit_server.make_server("127.0.0.1", 0, share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        try:
            _get_response(base, "/api/agents", headers={"Host": "audit.attacker.test"})
            raise AssertionError("unexpectedly accepted non-local Host header")
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode("utf-8"))
            assert e.code == 403, e.code
            assert body["error"] == "host_not_allowed", body
        print("✅ audit UI rejects non-local Host headers")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_api_serves_audit_and_agents()
    test_api_rejects_missing_subject()
    test_api_previews_trust_change_without_contents()
    test_html_is_safe_and_browser_testable()
    test_rejects_dns_rebinding_host_header()
    print("\n🎉 share-audit web API tests passed")
