"""
LinkedOut agent list（信任白名單）持久化管理。

agents.json 格式（flat dict，key = 對方公鑰 hex）：
{
  "1d11a9a3…": {"name": "B", "added": "2026-05-27"},
  "9f0a915…":  {"name": "Carol", "added": "2026-05-27"}
}

節點啟動時會載入這份清單，接受清單內「所有人」傳來的訊息，
不需在命令列逐一指定 --trust。未來可把每個 value 擴成 per-method/per-vault 權限。

CLI：
  python3 agents.py list
  python3 agents.py add <pubkey> --name B
  python3 agents.py remove <pubkey>
"""

import argparse
import json
import os
from datetime import date
from typing import Dict

DEFAULT_PATH = "agents.json"


def _valid_pubkey(pubkey: str) -> bool:
    """X25519 公鑰應為 32 bytes = 64 個 hex 字元。"""
    try:
        return len(bytes.fromhex(pubkey)) == 32
    except ValueError:
        return False


def load(path: str = DEFAULT_PATH) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save(agents: Dict[str, dict], path: str = DEFAULT_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)


def add(pubkey: str, name: str = "", path: str = DEFAULT_PATH) -> Dict[str, dict]:
    if not _valid_pubkey(pubkey):
        raise ValueError(f"不是合法的 X25519 公鑰（需 64 hex 字元）：{pubkey[:24]}…")
    agents = load(path)
    existing = agents.get(pubkey, {})
    agents[pubkey] = {
        "name": name or existing.get("name", ""),
        "added": existing.get("added", date.today().isoformat()),
    }
    save(agents, path)
    return agents


def remove(pubkey: str, path: str = DEFAULT_PATH) -> Dict[str, dict]:
    agents = load(path)
    agents.pop(pubkey, None)
    save(agents, path)
    return agents


def _print_list(agents: Dict[str, dict]):
    if not agents:
        print("（agent list 是空的；用 `python3 agents.py add <pubkey> --name X` 加入）")
        return
    print(f"🤝 agent list（{len(agents)} 人）:")
    for pk, meta in agents.items():
        print(f"   • {meta.get('name') or '(無名)':<10} {pk}  (added {meta.get('added','?')})")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="LinkedOut agent list（信任白名單）管理")
    p.add_argument("--file", default=DEFAULT_PATH, help=f"清單檔（預設 {DEFAULT_PATH}）")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="加入一把公鑰")
    pa.add_argument("pubkey")
    pa.add_argument("--name", default="")

    pr = sub.add_parser("remove", help="移除一把公鑰")
    pr.add_argument("pubkey")

    sub.add_parser("list", help="列出目前白名單")

    args = p.parse_args()
    if args.cmd == "add":
        _print_list(add(args.pubkey, args.name, args.file))
    elif args.cmd == "remove":
        _print_list(remove(args.pubkey, args.file))
    elif args.cmd == "list":
        _print_list(load(args.file))
