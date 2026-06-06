#!/usr/bin/env python3
"""
Offline exposure audit for LinkedOut share/ permissions.

This tool answers a practical operator question before starting a node:
"If this peer asks me to list/read/ask, what can they see at their current tier?"

It deliberately reports metadata and counts, not file contents.
"""

import argparse
import json
import os
from typing import Dict, Optional

import agents
import app_layer


def _entry_kind(entry: str) -> str:
    return "dir" if entry.endswith("/") else "file"


def _zone_access(zone: str) -> Dict[str, bool]:
    return {
        "read": True,
        "append": zone == app_layer.ZONE_APPEND,
    }


def resolve_peer(pubkey: Optional[str], agents_file: str, tier: Optional[str]) -> Dict[str, str]:
    """Resolve audit subject from either explicit --tier or agents.json metadata."""
    if tier:
        return {
            "pubkey": pubkey or "",
            "name": "",
            "tier": agents.normalize_tier(tier),
            "source": "explicit",
        }
    if not pubkey:
        raise ValueError("provide either --tier or --peer-pubkey")

    meta = agents.load(agents_file).get(pubkey)
    if meta is None:
        raise ValueError(f"peer not found in {agents_file}: {pubkey[:16]}...")
    return {
        "pubkey": pubkey,
        "name": meta.get("name", ""),
        "tier": agents.get_tier(meta),
        "source": agents_file,
    }


def build_audit(share: str, tier: str, path: str = "") -> Dict:
    """Return a content-free view of what a tier can see under share/."""
    normalized = agents.normalize_tier(tier)
    visible = app_layer._zones_for_tier(normalized)
    list_req = app_layer.FileRequest.model_validate(app_layer.make_request("list", path=path))
    listing = app_layer._do_list(list_req, share, normalized)
    ctx = app_layer._collect_ask_context(share, normalized)

    zones = []
    for zone in app_layer.ALL_ZONES:
        zones.append({
            "zone": zone,
            "visible": zone in visible,
            "requires": app_layer.ZONE_MIN_TIER[zone],
            **(_zone_access(zone) if zone in visible else {"read": False, "append": False}),
        })

    entries = []
    if listing.ok and listing.entries:
        entries = [{"path": entry, "kind": _entry_kind(entry)} for entry in listing.entries]

    return {
        "share": os.path.realpath(share),
        "tier": normalized,
        "path": path,
        "list": {
            "ok": listing.ok,
            "error": listing.error,
            "entries": entries,
        },
        "zones": zones,
        "ask_context": {
            "text_chunks": len(ctx),
            "bytes": sum(len(chunk.encode("utf-8")) for chunk in ctx),
            "limit_bytes": app_layer._ASK_CTX_LIMIT,
            "content_included": False,
        },
    }


def print_audit(audit: Dict, peer: Dict[str, str]) -> None:
    label = peer.get("name") or (peer.get("pubkey") or "explicit tier")[:16]
    print("LinkedOut exposure audit")
    print(f"Peer: {label}")
    if peer.get("pubkey"):
        print(f"Pubkey: {peer['pubkey']}")
    print(f"Tier: {audit['tier']} (source: {peer.get('source', 'unknown')})")
    print(f"Share: {audit['share']}")
    if audit.get("path"):
        print(f"Path filter: {audit['path']}")

    print("\nZones:")
    for zone in audit["zones"]:
        if zone["visible"]:
            caps = "read+append" if zone["append"] else "read"
            print(f"  - {zone['zone']}/ visible ({caps})")
        else:
            print(f"  - {zone['zone']}/ hidden (requires {zone['requires']})")

    print("\nVisible listing:")
    listing = audit["list"]
    if not listing["ok"]:
        print(f"  error: {listing['error']}")
    elif not listing["entries"]:
        print("  (empty)")
    else:
        for entry in listing["entries"]:
            suffix = "/" if entry["kind"] == "dir" and not entry["path"].endswith("/") else ""
            print(f"  - {entry['path']}{suffix}")

    ctx = audit["ask_context"]
    print("\nAsk exposure:")
    print(f"  text chunks: {ctx['text_chunks']}")
    print(f"  bytes: {ctx['bytes']} / {ctx['limit_bytes']}")
    print("  file contents: not printed by this audit")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit what a LinkedOut peer tier can see in share/")
    parser.add_argument("--share", default="share", help="share directory to audit")
    parser.add_argument("--agents-file", default=agents.DEFAULT_PATH, help="agents.json path")
    parser.add_argument("--peer-pubkey", default=None, help="peer public key to look up in agents.json")
    parser.add_argument("--tier", default=None, choices=agents.TIERS,
                        help="audit an explicit tier instead of looking up a peer")
    parser.add_argument("--path", default="", help="optional share-relative path to list")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    try:
        peer = resolve_peer(args.peer_pubkey, args.agents_file, args.tier)
        audit = build_audit(args.share, peer["tier"], args.path)
    except ValueError as e:
        print(f"error: {e}")
        return 2

    if args.json:
        print(json.dumps({"peer": peer, "audit": audit}, ensure_ascii=False, indent=2))
    else:
        print_audit(audit, peer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
