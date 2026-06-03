#!/usr/bin/env python3
"""
score.py — 把一段最終答案文字，對 ground_truth.json 的 N 個關鍵事實打分。

用法：
    python3 score.py <summary.txt> [--label baseline]
    cat summary.txt | python3 score.py - [--label v1]

打分規則：每個 fact 只要它任一 keyword（大小寫不敏感）出現在文字裡，就算命中。
輸出每個 fact 命中與否、SCORE: n/總數，並印一行機器可讀的 JSON。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GT_PATH = os.path.join(HERE, "ground_truth.json")


def load_facts():
    with open(GT_PATH, encoding="utf-8") as f:
        return json.load(f)["facts"]


def score(text: str, facts: list) -> dict:
    low = text.lower()
    hits = []
    for fact in facts:
        matched = next((kw for kw in fact["keywords"] if kw.lower() in low), None)
        hits.append({"id": fact["id"], "hit": matched is not None,
                     "via": matched, "owner": fact.get("owner")})
    n = sum(1 for h in hits if h["hit"])
    return {"score": n, "total": len(facts), "hits": hits}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("summary", help="答案文字檔路徑，或 - 代表 stdin")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    text = sys.stdin.read() if args.summary == "-" else open(args.summary, encoding="utf-8").read()
    facts = load_facts()
    res = score(text, facts)

    tag = f"[{args.label}] " if args.label else ""
    print(f"── {tag}score ──")
    for h in res["hits"]:
        mark = "✅" if h["hit"] else "❌"
        owner = h["owner"]
        via = f"（命中關鍵字：{h['via']}）" if h["hit"] else "（缺，這條在 " + str(owner) + " 手上）"
        print(f"  {mark} {h['id']:<14} {via}")
    print(f"  SCORE: {res['score']}/{res['total']}")
    print("JSON " + json.dumps({"label": args.label, **res}, ensure_ascii=False))
