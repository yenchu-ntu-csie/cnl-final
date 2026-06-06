#!/usr/bin/env python3
"""
Offline exposure-audit tests. No network and no Ollama required.

Run: .venv/bin/python test_share_audit.py
"""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import agents       # noqa: E402
import app_layer    # noqa: E402
import share_audit  # noqa: E402


def _seed_share():
    work = tempfile.mkdtemp()
    share = os.path.join(work, "share")
    app_layer.ensure_share(share)
    files = {
        ("read-only", "public.md"): "PUBLIC alpha",
        ("read&append", "log.md"): "LOG beta",
        ("task", "task.md"): "TASK gamma",
        ("personal", "secret.md"): "SECRET delta",
    }
    for (zone, name), body in files.items():
        with open(os.path.join(share, zone, name), "w", encoding="utf-8") as f:
            f.write(body)
    return work, share


def test_common_audit_hides_task_and_personal():
    _, share = _seed_share()
    audit = share_audit.build_audit(share, "common")
    paths = {entry["path"] for entry in audit["list"]["entries"]}
    assert "read-only/public.md" in paths, paths
    assert "read&append/log.md" in paths, paths
    assert "task/task.md" not in paths, paths
    assert "personal/secret.md" not in paths, paths
    assert audit["ask_context"]["content_included"] is False
    assert audit["ask_context"]["text_chunks"] == 2, audit["ask_context"]
    print("✅ common audit hides task/personal and reports content-free context stats")


def test_task_audit_shows_task_not_personal():
    _, share = _seed_share()
    audit = share_audit.build_audit(share, "task")
    paths = {entry["path"] for entry in audit["list"]["entries"]}
    assert "task/task.md" in paths, paths
    assert "personal/secret.md" not in paths, paths
    task_zone = next(zone for zone in audit["zones"] if zone["zone"] == "task")
    personal_zone = next(zone for zone in audit["zones"] if zone["zone"] == "personal")
    assert task_zone["visible"] is True
    assert personal_zone["visible"] is False
    print("✅ task audit includes task zone and hides personal zone")


def test_peer_resolution_uses_agents_file_tier():
    work, share = _seed_share()
    af = os.path.join(work, "agents.json")
    pub = "a" * 64
    agents.add(pub, "Alice", af, tier="personal")
    peer = share_audit.resolve_peer(pub, af, tier=None)
    audit = share_audit.build_audit(share, peer["tier"])
    assert peer["name"] == "Alice", peer
    assert peer["tier"] == "personal", peer
    paths = {entry["path"] for entry in audit["list"]["entries"]}
    assert "personal/secret.md" in paths, paths
    print("✅ peer audit resolves tier/name from agents.json")


def test_json_output_does_not_include_file_contents():
    _, share = _seed_share()
    peer = {"pubkey": "", "name": "", "tier": "personal", "source": "explicit"}
    audit = share_audit.build_audit(share, "personal")
    rendered = json.dumps({"peer": peer, "audit": audit}, ensure_ascii=False)
    assert "SECRET delta" not in rendered, rendered
    assert "PUBLIC alpha" not in rendered, rendered
    assert "personal/secret.md" in rendered, rendered
    print("✅ machine-readable audit includes paths but not file contents")


if __name__ == "__main__":
    test_common_audit_hides_task_and_personal()
    test_task_audit_shows_task_not_personal()
    test_peer_resolution_uses_agents_file_tier()
    test_json_output_does_not_include_file_contents()
    print("\n🎉 exposure-audit tests passed")
