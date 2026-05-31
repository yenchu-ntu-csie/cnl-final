#!/usr/bin/env python3
"""
S4 demo — 在 scenario D 上量「多跳知識路由」是否補上搆不到的那 1/3。

重用 run_scenario.py 的設定函式（import 成 module，不去改它），
跑：baseline → v1（直接朋友）→ S4（--route 多跳），三者對 GT 打分。
預期：0/3 → 2/3 → 3/3（最後一條 CUDA 11.4 由 Carol 代轉到 Dave 拿回）。

用法（repo 根目錄、.venv、ollama 在跑）：
    .venv/bin/python scenario/D_gpu/run_s4_demo.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_scenario as rs    # 重用設定/打分（不修改它 → 不撞 Felicity 的本機改動）


def run_route(goal, pub, ttl=2):
    """Bob 用 --route 對信任圖發查詢；回傳 (最終整理, 完整 log)。"""
    cmd = [rs.PY, "-u", rs.P2P, "--port", str(rs.PORTS["bob"]), "--name", "Bob",
           "--key-file", os.path.join(rs.RUN, "bob", "node.key"),
           "--agents-file", os.path.join(rs.RUN, "bob", "agents.json"),
           "--share", os.path.join(rs.RUN, "bob", "share"),
           "--server-ip", "127.0.0.1", "--server-port", str(rs.RELAY_PORT),
           "--route", "--goal", goal, "--ttl", str(ttl)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    out = res.stdout + res.stderr
    marker = "最終整理"
    idx = out.rfind(marker)
    summary = out[idx + len(marker):].lstrip("：:\n ") if idx >= 0 else out
    return summary, out


def main():
    if not rs.ollama_up():
        print("❌ ollama 沒在跑"); sys.exit(1)
    import json
    goal = json.load(open(os.path.join(HERE, "ground_truth.json"), encoding="utf-8"))["goal"]
    print("🎯 goal：", goal, "\n")

    pub = rs.setup_workdir()
    rs.build_key_graph(pub)
    print("🤝 金鑰圖：Bob→{Alice,Carol}；Carol→{Bob,Dave}；Dave→{Carol}（Bob 搆不到 Dave）\n")

    procs = []
    try:
        procs.append(subprocess.Popen([rs.PY, "-u", rs.RELAY, "--port", str(rs.RELAY_PORT)],
                     stdout=open(os.path.join(rs.RUN, "relay.log"), "w"), stderr=subprocess.STDOUT))
        time.sleep(1)
        for who in ("alice", "carol", "dave"):
            p, _ = rs.start_node(who, [])
            procs.append(p)
        for who in ("alice", "carol", "dave"):
            ok = rs.wait_for(os.path.join(rs.RUN, f"{who}.log"), "Registered as", timeout=20)
            print(f"   {'🟢' if ok else '🔴'} {who}")
        print()

        # baseline
        print("════════ baseline（Bob 只用自己 vault）════════")
        base = rs.run_baseline(goal, os.path.join(rs.RUN, "bob", "share"))
        base_res = rs.show_score("baseline", base)
        print()

        # v1（直接朋友）
        print("════════ v1（--auto 群組：問直接朋友 Alice/Carol）════════")
        v1_sum, _ = rs.run_v1(goal, pub)
        v1_res = rs.show_score("v1", v1_sum)
        print()

        # S4（多跳路由）
        print("════════ S4（--route 多跳：Carol 代轉到 Dave）════════")
        s4_sum, s4_log = run_route(goal, pub, ttl=2)
        for line in s4_log.splitlines():
            if "[Route]" in line and ("轉發" in line or "有料" in line or "收到答案" in line or "收集到" in line):
                print("  ", line.strip())
        print("   ── S4 最終答案 ──")
        for line in s4_sum.splitlines()[:12]:
            print("   │", line)
        s4_res = rs.show_score("s4", s4_sum)
        print()

        print("════════ 對照 ════════")
        print(f"   baseline : {base_res['score']}/{base_res['total']}")
        print(f"   v1       : {v1_res['score']}/{v1_res['total']}")
        print(f"   S4       : {s4_res['score']}/{s4_res['total']}")
        print("\n預期 0 → 2 → 3：S4 多跳把『只有 Dave 知道的 CUDA 11.4』經 Carol 代轉回來。")
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
