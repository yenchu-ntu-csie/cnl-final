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


def _post_json(base, path, **payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as res:
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


def test_api_plans_ask_without_contents_or_hidden_paths():
    _, share, agents_file, pub = _seed()
    server = share_audit_server.make_server("127.0.0.1", 0, share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        plan = _post_json(
            base,
            "/api/ask-plan",
            share=share,
            agents_file=agents_file,
            peer_pubkey=pub,
            mode="remote",
            query="Can Bob ask about task and personal context?",
        )
        rendered = json.dumps(plan, ensure_ascii=False)
        ask_plan = plan["ask_plan"]
        assert plan["ok"] is True, plan
        assert ask_plan["mode"] == "remote", plan
        assert ask_plan["tier"] == "task", plan
        assert ask_plan["readiness"]["state"] == "ask constrained", plan
        assert "task" in ask_plan["visible_zones"], plan
        assert "personal" in ask_plan["hidden_zones"], plan
        assert "personal" in ask_plan["mentioned_hidden_zones"], plan
        assert ask_plan["ask_context"]["content_included"] is False, plan
        assert "--op ask --mode remote" in ask_plan["command"], plan
        assert pub in ask_plan["command"], plan
        assert "SECRET gamma" not in rendered, rendered
        assert "TASK beta" not in rendered, rendered
        assert "PUBLIC alpha" not in rendered, rendered
        assert "personal/secret.md" not in rendered, rendered
        try:
            _get_json(base, "/api/ask-plan")
            raise AssertionError("GET ask-plan should not be available")
        except urllib.error.HTTPError as e:
            assert e.code == 404, e.code
        print("✅ audit UI API plans ask flow without file contents or hidden paths")
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


def test_api_serves_peer_matrix_without_contents_or_paths():
    _, share, agents_file, pub = _seed()
    agents.add("a" * 64, "Alice", agents_file, tier="common")
    agents.add("c" * 64, "Carol", agents_file, tier="personal")
    server = share_audit_server.make_server("127.0.0.1", 0, share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        matrix = _get_json(base, "/api/matrix", share=share, agents_file=agents_file)
        rendered = json.dumps(matrix, ensure_ascii=False)
        by_name = {row["name"]: row for row in matrix["matrix"]}
        assert matrix["ok"] is True, matrix
        assert matrix["peer_count"] == 3, matrix
        assert by_name["Alice"]["tier"] == "common", by_name
        assert by_name["Bob"]["pubkey"] == pub, by_name
        assert by_name["Carol"]["tier"] == "personal", by_name
        assert by_name["Carol"]["personal_tier"] is True, by_name["Carol"]
        assert by_name["Carol"]["visible_zone_count"] > by_name["Bob"]["visible_zone_count"], by_name
        assert by_name["Bob"]["entry_count"] > by_name["Alice"]["entry_count"], by_name
        assert by_name["Carol"]["ask_context"]["chunks"] > by_name["Bob"]["ask_context"]["chunks"], by_name
        assert "bytes" not in by_name["Carol"]["ask_context"], by_name["Carol"]
        assert "personal" in by_name["Carol"]["visible_zones"], by_name["Carol"]
        assert "entries" not in by_name["Carol"], by_name["Carol"]
        assert "SECRET gamma" not in rendered, rendered
        assert "TASK beta" not in rendered, rendered
        assert "PUBLIC alpha" not in rendered, rendered
        assert "personal/secret.md" not in rendered, rendered
        print("✅ audit UI API serves content-free peer exposure matrix")
    finally:
        server.shutdown()
        server.server_close()


def test_api_serves_local_self_audit_without_contents():
    _, share, agents_file, _ = _seed()
    server = share_audit_server.make_server("127.0.0.1", 0, share, agents_file)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        self_audit = _get_json(base, "/api/self", share=share, agents_file=agents_file)
        rendered = json.dumps(self_audit, ensure_ascii=False)
        by_zone = {zone["zone"]: zone for zone in self_audit["zones"]}
        assert self_audit["ok"] is True, self_audit
        assert self_audit["local_only"] is True, self_audit
        assert self_audit["host_label"] == "this computer", self_audit
        assert self_audit["friend_count"] == 1, self_audit
        assert by_zone["read-only"]["file_count"] == 1, by_zone
        assert by_zone["task"]["file_count"] == 1, by_zone
        assert by_zone["personal"]["file_count"] == 1, by_zone
        assert "SECRET gamma" not in rendered, rendered
        assert "TASK beta" not in rendered, rendered
        assert "PUBLIC alpha" not in rendered, rendered
        print("✅ audit UI API serves local self-audit without file contents")
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
            assert 'data-testid="self-audit-panel"' in body, body
            assert 'data-testid="ask-composer-panel"' in body, body
            assert 'data-testid="plan-ask"' in body, body
            assert "/api/ask-plan" in body, body
            assert 'data-testid="scenario-panel"' in body, body
            assert 'data-scenario-run="' in body, body
            assert 'data-testid="run-audit"' in body, body
            assert 'data-matrix-action="inspect"' in body, body
            assert 'data-matrix-action="preview"' in body, body
            assert "Bob Scenario Walkthrough" in body, body
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
    test_api_plans_ask_without_contents_or_hidden_paths()
    test_api_previews_trust_change_without_contents()
    test_api_serves_peer_matrix_without_contents_or_paths()
    test_api_serves_local_self_audit_without_contents()
    test_html_is_safe_and_browser_testable()
    test_rejects_dns_rebinding_host_header()
    print("\n🎉 share-audit web API tests passed")
