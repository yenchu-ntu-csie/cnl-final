#!/usr/bin/env python3
"""
score.py — 對 scenario F 的最終 summary 打分。

兩種訊號：
  • facts_collected：3 條方程式線索，命中幾條（keyword 比對，跟 D/E 一致）
  • answer：對 / 「兩解都列」（partial）/ 錯（拿到 (4,6)）/ 沒解

facts_collected 反映「Bob 有沒有問到對的人」
answer 反映「下游合成能不能正確收斂」
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GT_PATH = os.path.join(HERE, "ground_truth.json")


def load_gt():
    with open(GT_PATH, encoding="utf-8") as f:
        return json.load(f)


def score_facts(text: str, facts: list) -> dict:
    low = text.lower()
    hits = []
    for fact in facts:
        matched = next((kw for kw in fact["keywords"] if kw.lower() in low), None)
        hits.append({"id": fact["id"], "hit": matched is not None,
                     "via": matched, "owner": fact.get("owner")})
    n = sum(1 for h in hits if h["hit"])
    return {"score": n, "total": len(facts), "hits": hits}


def detect_answer(text: str, gt: dict) -> dict:
    """回傳 {"verdict": one of correct|both|wrong|none, "matches": [...]}.
       correct = 只看到 (6,4) 寫法；both = 同時提到 (6,4) 跟 (4,6)；wrong = 只看到 (4,6)；none = 都沒。"""
    flat = re.sub(r"\s+", " ", text)
    has_correct = any(re.search(p, flat, re.IGNORECASE | re.DOTALL)
                      for p in gt["answer_check"]["correct_patterns"])
    has_wrong = any(re.search(p, flat, re.IGNORECASE | re.DOTALL)
                    for p in gt["answer_check"]["wrong_patterns"])
    if has_correct and has_wrong:
        verdict = "both"        # 提到兩個候選 → partial（A+B 還沒用 C 縮）
    elif has_correct:
        verdict = "correct"
    elif has_wrong:
        verdict = "wrong"
    else:
        verdict = "none"
    return {"verdict": verdict, "has_correct": has_correct, "has_wrong": has_wrong}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("summary", help="summary 文字檔；- 代表 stdin")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    text = sys.stdin.read() if args.summary == "-" else open(args.summary, encoding="utf-8").read()
    gt = load_gt()
    fr = score_facts(text, gt["facts"])
    ar = detect_answer(text, gt)

    tag = f"[{args.label}] " if args.label else ""
    print(f"── {tag}facts collected ──")
    for h in fr["hits"]:
        mark = "✅" if h["hit"] else "❌"
        owner = h["owner"]
        via = f"（命中關鍵字：{h['via']}）" if h["hit"] else f"（缺，在 {owner} 手上）"
        print(f"  {mark} {h['id']:<14} {via}")
    print(f"  facts: {fr['score']}/{fr['total']}")
    print(f"── {tag}final answer ──")
    icon = {"correct": "✅", "both": "🟡", "wrong": "❌", "none": "❌"}[ar["verdict"]]
    print(f"  {icon} verdict = {ar['verdict']}  "
          f"(has_correct={ar['has_correct']}, has_wrong={ar['has_wrong']})")
    print("JSON " + json.dumps({"label": args.label, **fr, "answer": ar}, ensure_ascii=False))
