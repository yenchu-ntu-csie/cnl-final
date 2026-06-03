"""
LinkedOut agent list（信任白名單）持久化管理。

agents.json 格式（flat dict，key = 對方公鑰 hex）：
{
  "1d11a9a3…": {"name": "B", "added": "2026-05-27", "tier": "common"},
  "9f0a915…":  {"name": "Carol", "added": "2026-05-27", "tier": "task"}
}

節點啟動時會載入這份清單，接受清單內「所有人」傳來的訊息，
不需在命令列逐一指定 --trust。

權限分層（tier）：每個 peer 有一個 tier，決定他看得到 share/ 的哪幾區：
  common（預設）< task < personal
缺 tier 欄位 → 視為 common（向下相容舊的 agents.json）。

CLI：
  python3 agents.py list
  python3 agents.py add <pubkey> --name B [--tier task]
  python3 agents.py set-tier <pubkey> <tier>
  python3 agents.py remove <pubkey>
"""

import argparse
import json
import os
from datetime import date
from typing import Dict

DEFAULT_PATH = "agents.json"

# 權限層級（由低到高）；只在這裡宣告一次，app_layer 從這裡 import 引用。
TIERS = ("common", "task", "personal")
DEFAULT_TIER = "common"


def normalize_tier(tier: str) -> str:
    """驗證並正規化 tier（大小寫不敏感）。不合法則 raise。"""
    t = (tier or DEFAULT_TIER).strip().lower()
    if t not in TIERS:
        raise ValueError(f"不合法的 tier：{tier!r}（需為 {'/'.join(TIERS)} 之一）")
    return t


def _valid_pubkey(pubkey: str) -> bool:
    """X25519 公鑰應為 32 bytes = 64 個 hex 字元。"""
    try:
        return len(bytes.fromhex(pubkey)) == 32
    except ValueError:
        return False


def load(path: str = DEFAULT_PATH) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # 空檔 / 壞掉的 JSON → 當成空清單，別讓節點啟動就 crash
        return {}
    return data if isinstance(data, dict) else {}


def save(agents: Dict[str, dict], path: str = DEFAULT_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)


def get_tier(meta: dict) -> str:
    """從一筆 agent meta 取 tier；缺欄位 / 不合法都退回 common（讀取端寬鬆）。"""
    try:
        return normalize_tier(meta.get("tier", DEFAULT_TIER))
    except ValueError:
        return DEFAULT_TIER


# ── 智慧路由用的「聲望」(reputation)：學「往這個 next-hop 轉，該主題答得回來」──
# meta["rep"] = {keyword: score}；像 distance-vector 的 metric，從回饋累積。
_STOP = {"the", "and", "for", "with", "your", "you", "this", "that", "what",
         "how", "are", "into", "from", "我想", "請給", "關鍵", "設定", "做法"}

def topic_keywords(text: str, k: int = 8) -> list:
    """從一段文字粗略抽主題關鍵字（小寫、去短詞/停用詞）。中英皆可，純啟發式。"""
    import re as _re
    toks = _re.findall(r"[A-Za-z0-9_.\-]{3,}|[一-鿿]{2,}", (text or "").lower())
    out, seen = [], set()
    for t in toks:
        if t in _STOP or t in seen:
            continue
        seen.add(t); out.append(t)
        if len(out) >= k:
            break
    return out


def rep_score(meta: dict, kws) -> float:
    """這個 peer 對這些關鍵字的累積聲望分數（越高 = 越該往他轉）。"""
    rep = (meta or {}).get("rep", {}) or {}
    return float(sum(rep.get(kw, 0) for kw in kws))


def bump_rep(pubkey: str, kws, path: str = DEFAULT_PATH, amount: float = 1.0) -> None:
    """回饋學習：替某 next-hop 在這些關鍵字「加/減」分（持久化）。
    amount>0 = 獎勵（帶回答案）；amount<0 = 懲罰（轉了卻沒貢獻）。
    分數下限 0：降到 0 的關鍵字會被移除 → 該主題回到冷啟動（會退場、不會永久卡高分）。"""
    if not kws:
        return
    a = load(path)
    if pubkey not in a:
        return
    rep = a[pubkey].setdefault("rep", {})
    for kw in kws:
        v = float(rep.get(kw, 0)) + amount
        if v > 0:
            rep[kw] = v
        else:
            rep.pop(kw, None)          # 降到 0 → 移除（回到冷啟動）
    if not rep:
        a[pubkey].pop("rep", None)     # rep 空了就清掉，保持檔案乾淨
    save(a, path)


def add(pubkey: str, name: str = "", path: str = DEFAULT_PATH,
        tier: str = None) -> Dict[str, dict]:
    if not _valid_pubkey(pubkey):
        raise ValueError(f"不是合法的 X25519 公鑰（需 64 hex 字元）：{pubkey[:24]}…")
    agents = load(path)
    existing = agents.get(pubkey, {})
    # tier=None → 沿用既有的；既有也沒有 → common
    resolved_tier = normalize_tier(tier) if tier is not None \
        else get_tier(existing)
    agents[pubkey] = {
        "name": name or existing.get("name", ""),
        "added": existing.get("added", date.today().isoformat()),
        "tier": resolved_tier,
    }
    save(agents, path)
    return agents


def set_tier(pubkey: str, tier: str, path: str = DEFAULT_PATH) -> Dict[str, dict]:
    """改某個既有 peer 的 tier（不必重新 add）。peer 不存在則 raise。"""
    resolved = normalize_tier(tier)
    agents = load(path)
    if pubkey not in agents:
        raise ValueError(f"agent list 裡沒有這把公鑰：{pubkey[:24]}…（先 add 再 set-tier）")
    agents[pubkey]["tier"] = resolved
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
        name = meta.get('name') or '(無名)'
        print(f"   • {name:<10} [{get_tier(meta):<8}] {pk}  (added {meta.get('added','?')})")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="LinkedOut agent list（信任白名單）管理")
    p.add_argument("--file", default=DEFAULT_PATH, help=f"清單檔（預設 {DEFAULT_PATH}）")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="加入一把公鑰")
    pa.add_argument("pubkey")
    pa.add_argument("--name", default="")
    pa.add_argument("--tier", default=None, choices=TIERS,
                    help=f"權限層級（{'/'.join(TIERS)}；預設 {DEFAULT_TIER}）")

    pst = sub.add_parser("set-tier", help="改既有 peer 的 tier")
    pst.add_argument("pubkey")
    pst.add_argument("tier", choices=TIERS)

    pr = sub.add_parser("remove", help="移除一把公鑰")
    pr.add_argument("pubkey")

    sub.add_parser("list", help="列出目前白名單")

    args = p.parse_args()
    try:
        if args.cmd == "add":
            _print_list(add(args.pubkey, args.name, args.file, tier=args.tier))
        elif args.cmd == "set-tier":
            _print_list(set_tier(args.pubkey, args.tier, args.file))
        elif args.cmd == "remove":
            _print_list(remove(args.pubkey, args.file))
        elif args.cmd == "list":
            _print_list(load(args.file))
    except ValueError as e:
        print(f"⚠️  {e}")
        raise SystemExit(1)
