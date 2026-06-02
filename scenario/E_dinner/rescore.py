#!/usr/bin/env python3
"""
rescore.py — 不重跑實驗，用更嚴格的 keyword 對已存的 bob_<cell>.log 重新打分。

讀 run/bob_*.log 內「最終整理：」之後的文字，餵進 score()。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from score import load_facts, score as score_text

CELLS = [
    ("A_all_common",               "全 common", 1),
    ("B_carol_task",               "Carol→task", 2),
    ("C_dave_personal",            "Dave→personal", 2),
    ("D_carol_task_dave_personal", "Carol+Dave 雙開", 3),
]


def extract_summary(logpath):
    with open(logpath, encoding="utf-8") as f:
        txt = f.read()
    idx = txt.rfind("最終整理")
    if idx < 0:
        return ""
    return txt[idx + len("最終整理"):].lstrip("：:\n ")


def main():
    facts = load_facts()
    print("📋 新 keywords:")
    for f in facts:
        print(f"   {f['id']:<14} → {f['keywords']}")
    print()

    results = {}
    for cid, label, expect in CELLS:
        log = os.path.join(HERE, "run", f"bob_{cid}.log")
        if not os.path.exists(log):
            print(f"⚠️  {log} 不存在")
            continue
        summary = extract_summary(log)
        res = score_text(summary, facts)
        results[cid] = {**res, "label": label, "expect": expect, "summary": summary}

    # 矩陣
    print("═════════════ 重算結果矩陣 ═════════════")
    headers = [f["id"] for f in facts]
    print(f"{'cell':<32}  " + " ".join(f"{h:<14}" for h in headers) +
          f"  {'score':<6} {'expect':<6}  {'判定':<6}")
    print("─" * 110)
    for cid, info in results.items():
        cells_v = [("✅" if h["hit"] else "❌") for h in info["hits"]]
        ok = info["score"] == info["expect"]
        verdict = "✅ 對齊" if ok else f"⚠️ 差{info['score']-info['expect']:+d}"
        print(f"{cid:<32}  " + " ".join(f"{v:<14}" for v in cells_v) +
              f"  {info['score']}/{info['total']}    {info['expect']}/3       {verdict}")
    print()

    # 詳細命中
    print("════ 各 cell 細節 ════")
    for cid, info in results.items():
        print(f"\n● {cid}（{info['label']}, 預期 {info['expect']}/3）")
        for h in info["hits"]:
            mark = "✅" if h["hit"] else "❌"
            via = f"命中字「{h['via']}」" if h["hit"] else f"缺（owner={h['owner']}）"
            print(f"   {mark} {h['id']:<14} {via}")


if __name__ == "__main__":
    main()
