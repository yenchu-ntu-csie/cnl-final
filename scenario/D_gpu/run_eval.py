#!/usr/bin/env python3
"""
Phase 2 量測 harness — 在 Scenario D 上跑 n 次、四組對照，輸出可放報告的結構化結果。

四組（同一張稀疏金鑰圖、同一份資料）：
  baseline : Bob 只用自己 vault（不問人）
  v1       : Bob --auto 群組（capability scan + 問直接朋友 Alice/Carol）
  S4       : Bob --route --ttl 2（多跳，Carol 代轉到 Dave）
  S4-ttl0  : Bob --route --ttl 0（ablation：只問直接朋友、不轉發 → 應拿不到 Dave）

LLM 有隨機性，所以跑 n 次看「分數分佈」，而不是單次。
模型可攜：--model 會設成所有子程序的 LINKEDOUT_MODEL（不指定就用各機器 resolve 到的）。

用法（repo 根目錄、.venv、ollama 在跑）：
  .venv/bin/python scenario/D_gpu/run_eval.py --n 5 --model qwen2.5:14b
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_scenario as rs          # 重用 setup/啟動/baseline/v1/打分
import run_s4_demo as s4           # 重用 run_route
from score import load_facts, score as score_text

FACTS = load_facts()
FACT_IDS = [f["id"] for f in FACTS]


def eval_text(text):
    """回傳 (總分, {fact_id: hit_bool})。"""
    r = score_text(text, FACTS)
    return r["score"], {h["id"]: h["hit"] for h in r["hits"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="每組重複次數")
    ap.add_argument("--model", default=None, help="設給所有子程序的 LINKEDOUT_MODEL（不給則各自 resolve）")
    ap.add_argument("--ttl", type=int, default=2, help="S4 的 ttl（預設 2）")
    args = ap.parse_args()

    if args.model:
        os.environ["LINKEDOUT_MODEL"] = args.model     # 子程序 + in-process baseline 都吃這個
    if not rs.ollama_up():
        print("❌ ollama 沒在跑"); sys.exit(1)

    goal = json.load(open(os.path.join(HERE, "ground_truth.json"), encoding="utf-8"))["goal"]
    total = len(FACTS)
    print(f"🎯 n={args.n}  model={args.model or '(resolve)'}  goal 命中滿分={total}\n")

    pub = rs.setup_workdir()
    rs.build_key_graph(pub)

    GROUPS = ("baseline", "v1", "S4", "S4-ttl0")
    scores = {g: [] for g in GROUPS}                       # g → [score,...]
    fact_hits = {g: {fid: 0 for fid in FACT_IDS} for g in GROUPS}  # g → fid → 命中次數
    procs = []

    def record(g, text):
        s, hits = eval_text(text)
        scores[g].append(s)
        for fid, hit in hits.items():
            if hit:
                fact_hits[g][fid] += 1
        return s
    try:
        procs.append(subprocess.Popen([rs.PY, "-u", rs.RELAY, "--port", str(rs.RELAY_PORT)],
                     stdout=open(os.path.join(rs.RUN, "relay.log"), "w"), stderr=subprocess.STDOUT))
        time.sleep(1)
        for who in ("alice", "carol", "dave"):
            p, _ = rs.start_node(who, [])
            procs.append(p)
        for who in ("alice", "carol", "dave"):
            rs.wait_for(os.path.join(rs.RUN, f"{who}.log"), "Registered as", timeout=20)
        print("🟢 relay + Alice/Carol/Dave 就緒\n")

        for i in range(1, args.n + 1):
            print(f"──────── run {i}/{args.n} ────────")
            print(f"  baseline : {record('baseline', rs.run_baseline(goal, os.path.join(rs.RUN, 'bob', 'share')))}/{total}")
            print(f"  v1       : {record('v1', rs.run_v1(goal, pub)[0])}/{total}")
            print(f"  S4(ttl={args.ttl}) : {record('S4', s4.run_route(goal, pub, ttl=args.ttl)[0])}/{total}")
            print(f"  S4-ttl0  : {record('S4-ttl0', s4.run_route(goal, pub, ttl=0)[0])}/{total}   (ablation)")
            print()
    finally:
        for p in procs:
            p.terminate()

    # 彙整
    def stats(xs):
        return {"runs": xs, "mean": round(sum(xs) / len(xs), 2) if xs else 0,
                "max": max(xs) if xs else 0, "min": min(xs) if xs else 0}
    summary = {
        "n": args.n, "model": args.model, "total": total,
        "fact_owner": {f["id"]: f.get("owner") for f in FACTS},
        "score": {g: stats(scores[g]) for g in GROUPS},
        "fact_hit_rate": {g: {fid: round(fact_hits[g][fid] / args.n, 2) for fid in FACT_IDS} for g in GROUPS},
    }
    out = os.path.join(rs.RUN, "eval_results.json")
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("════════ 總分（mean / min–max over n）════════")
    print(f"  {'group':<10} {'mean':>5}  {'min–max'}")
    for g in GROUPS:
        st = summary["score"][g]
        print(f"  {g:<10} {st['mean']:>5}  {st['min']}–{st['max']}  {st['runs']}")

    print("\n════════ per-fact 命中率（對隨機性最 robust 的證據）════════")
    print(f"  {'fact':<14} {'owner':<7} " + "  ".join(f"{g:>8}" for g in GROUPS))
    for fid in FACT_IDS:
        owner = summary["fact_owner"][fid]
        cells = "  ".join(f"{summary['fact_hit_rate'][g][fid]:>8.0%}" for g in GROUPS)
        print(f"  {fid:<14} {owner:<7} {cells}")
    print(f"\n結果 JSON：{out}")
    print("敘事：driver 這條只在 S4 出現、baseline/v1/S4-ttl0 永遠 0 → 證明那分來自『多跳路由搆到 Dave』。")


if __name__ == "__main__":
    main()
