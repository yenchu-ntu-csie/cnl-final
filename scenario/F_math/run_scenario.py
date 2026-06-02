#!/usr/bin/env python3
"""
scenario F — 拼數學線索（資訊分散度 testbed）

故事：Bob 自己手上有一塊線索，另外兩塊在朋友手上。
    Bob  : x + y = 10       (sum)              ← 主持人自帶
    Alice: x × y = 24       (product)
    Carol: x > y            (inequality)
真值：(x, y) = (6, 4)。Bob 自己 → infinite；+Alice → {(6,4),(4,6)} 兩解；+Carol 才唯一 (6,4)。

3 個 cell：
    F1 baseline (Bob 自己)         → 1 fact (sum), no unique answer
    F2 partial  (Bob + Alice)      → 2 facts, ambiguous (期待 both 候選)
    F3 full     (Bob + Alice + Carol)→ 3 facts, correct (6,4)

衡量兩個訊號：
    facts_collected：summary 提到幾條線索（≤3）
    answer verdict：correct / both / wrong / none

用法：
    .venv/bin/python scenario/F_math/run_scenario.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import e2ee          # noqa: E402
import agents        # noqa: E402
import app_layer     # noqa: E402
import ai_client     # noqa: E402
from score import load_gt, score_facts, detect_answer  # noqa: E402

RUN = os.path.join(HERE, "run")
RELAY_PORT = 9720                                  # 跟 D(9700)/E(9710) 錯開
PORTS = {"alice": 8831, "carol": 8832, "bob": 8830}
PEERS = ("alice", "carol")
AGENTS = ("bob",) + PEERS
PY = sys.executable
P2P = os.path.join(ROOT, "p2p_node.py")
RELAY = os.path.join(ROOT, "relay_server.py")

CELLS = [
    {"id": "F1_baseline",  "label": "Bob 自己（只有 sum）",
     "peers": [],                   "mode": "baseline",
     "expect_facts": 1, "expect_answer": "none"},
    {"id": "F2_partial",   "label": "Bob + Alice（sum + product）",
     "peers": ["alice"],            "mode": "auto",
     "expect_facts": 2, "expect_answer": "both"},
    {"id": "F3_full",      "label": "Bob + Alice + Carol（全 3 條）",
     "peers": ["alice", "carol"],   "mode": "auto",
     "expect_facts": 3, "expect_answer": "correct"},
]


def ollama_up() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3).read()
        return True
    except Exception:
        return False


def sh_rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def setup_workdir():
    import shutil
    sh_rmtree(RUN)
    pub = {}
    for who in AGENTS:
        share = os.path.join(RUN, who, "share")
        app_layer.ensure_share(share)
        seed = os.path.join(HERE, "seeds", who)
        if os.path.isdir(seed):
            for zone in os.listdir(seed):
                src, dst = os.path.join(seed, zone), os.path.join(share, zone)
                if os.path.isdir(src):
                    os.makedirs(dst, exist_ok=True)
                    for fn in os.listdir(src):
                        shutil.copy2(os.path.join(src, fn), os.path.join(dst, fn))
        key = os.path.join(RUN, who, "node.key")
        pub[who] = e2ee.public_hex(e2ee.load_or_create_identity(key))
    return pub


def build_agents(pub):
    """全 common tier，所有人都能讀對方的 read-only/clue.md。"""
    bob_agf = os.path.join(RUN, "bob", "agents.json")
    if os.path.exists(bob_agf):
        os.remove(bob_agf)
    for p in PEERS:
        agents.add(pub[p], p.capitalize(), bob_agf)
    for p in PEERS:
        agf = os.path.join(RUN, p, "agents.json")
        if os.path.exists(agf):
            os.remove(agf)
        agents.add(pub["bob"], "Bob", agf)


PEER_MODEL = "qwen2.5:7b"   # peers 用 qwen：對簡單 query 不會 over-refuse
BOB_MODEL  = "llama3.1:8b"  # Bob 用 llama：解二次方程能力較強

def start_peer(who):
    logf = open(os.path.join(RUN, f"{who}.log"), "w")
    cmd = [PY, "-u", P2P, "--port", str(PORTS[who]), "--name", who.capitalize(),
           "--key-file", os.path.join(RUN, who, "node.key"),
           "--agents-file", os.path.join(RUN, who, "agents.json"),
           "--share", os.path.join(RUN, who, "share"),
           "--server-ip", "127.0.0.1", "--server-port", str(RELAY_PORT),
           "--model", PEER_MODEL]
    return subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT), logf


def wait_for(logpath, needle, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(logpath):
            with open(logpath, encoding="utf-8", errors="ignore") as f:
                if needle in f.read():
                    return True
        time.sleep(0.5)
    return False


def run_baseline(goal):
    """Bob 自己（不問人）。Bob 自己手上有 sum=10，但只有這一塊。"""
    own = app_layer._collect_ask_context(os.path.join(RUN, "bob", "share"), "personal")
    ctx = "\n\n".join(own) if own else "(Bob 沒有任何線索)"
    messages = [
        {"role": "system", "content":
            "You are Bob's local agent. Answer Bob's goal using ONLY the context below "
            "(Bob's own notes). The other clues (product, inequality) are in other people's hands and "
            "have NOT been collected. Do NOT invent the missing numbers. If you cannot uniquely "
            "determine x and y, say so clearly."},
        {"role": "user", "content": f"<<<CTX Bob's own notes>>>\n{ctx}\n<<<END CTX>>>\n\nGoal: {goal}"},
    ]
    return ai_client._call_sync(None, messages)


def run_auto(goal, pub, peers, cell_id):
    """Bob --auto 問指定的 peer。peers 是子集 of {alice, carol, dave}。"""
    peer_keys = ",".join(pub[p] for p in peers)
    cmd = [PY, "-u", P2P, "--port", str(PORTS["bob"]), "--name", "Bob",
           "--key-file", os.path.join(RUN, "bob", "node.key"),
           "--agents-file", os.path.join(RUN, "bob", "agents.json"),
           "--share", os.path.join(RUN, "bob", "share"),
           "--server-ip", "127.0.0.1", "--server-port", str(RELAY_PORT),
           "--peer-pubkey", peer_keys,
           "--auto", "--goal", goal, "--rounds", str(max(3, len(peers) + 1)),  # 多給輪次強迫每個 peer 都被問到
           "--model", BOB_MODEL]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    out = res.stdout + res.stderr
    with open(os.path.join(RUN, f"bob_{cell_id}.log"), "w", encoding="utf-8") as f:
        f.write(out)
    marker = "最終整理"
    idx = out.rfind(marker)
    return (out[idx + len(marker):].lstrip("：:\n ") if idx >= 0 else out), out


def kill_procs(procs):
    for p in procs:
        try: p.terminate()
        except Exception: pass
    time.sleep(0.5)
    for p in procs:
        try:
            if p.poll() is None: p.kill()
        except Exception: pass


def main():
    if not ollama_up():
        print("❌ ollama daemon 沒在跑（http://localhost:11434）。先 `ollama serve`。")
        sys.exit(1)

    gt = load_gt()
    goal = gt["goal"]
    print("🎯 goal：", goal[:80], "...\n")

    pub = setup_workdir()
    build_agents(pub)
    print("🔑 pubkeys：" + "  ".join(f"{w}={pub[w][:8]}…" for w in AGENTS))
    print("📐 真值：x=6, y=4（A:x+y=10, B:x*y=24, C:x>y）\n")

    # 起 relay + 三個 peer（cell 之間共用，因為沒要改 tier）
    pr = subprocess.Popen([PY, "-u", RELAY, "--port", str(RELAY_PORT)],
                          stdout=open(os.path.join(RUN, "relay.log"), "w"),
                          stderr=subprocess.STDOUT)
    time.sleep(1.5)
    procs = []
    for who in PEERS:
        p, _ = start_peer(who)
        procs.append(p)
    for who in PEERS:
        ok = wait_for(os.path.join(RUN, f"{who}.log"), "Registered as", timeout=20)
        print(f"   {'🟢' if ok else '🔴'} {who} {'registered' if ok else '上線失敗'}")
    print()

    results = {}
    try:
        for cell in CELLS:
            print(f"\n════════ {cell['id']}：{cell['label']} ════════")
            try:
                if cell["mode"] == "baseline":
                    summary = run_baseline(goal)
                    with open(os.path.join(RUN, f"bob_{cell['id']}.log"), "w", encoding="utf-8") as f:
                        f.write(summary)
                else:
                    summary, _ = run_auto(goal, pub, cell["peers"], cell["id"])

                for line in summary.splitlines()[:10]:
                    print("   │", line)

                fr = score_facts(summary, gt["facts"])
                ar = detect_answer(summary, gt)
                results[cell["id"]] = {
                    "facts": fr, "answer": ar,
                    "expect_facts": cell["expect_facts"],
                    "expect_answer": cell["expect_answer"],
                    "label": cell["label"],
                }
                print(f"   ── facts: {fr['score']}/{fr['total']}  "
                      f"(預期 {cell['expect_facts']}/3) ──")
                for h in fr["hits"]:
                    mark = "✅" if h["hit"] else "❌"
                    print(f"     {mark} {h['id']:<12} ({h['owner']})")
                icon = {"correct": "✅", "both": "🟡", "wrong": "❌", "none": "❌"}[ar["verdict"]]
                print(f"   ── answer: {icon} {ar['verdict']} (預期 {cell['expect_answer']}) ──")
            except Exception as e:
                print(f"   ❌ cell 失敗: {e}")
                results[cell["id"]] = {"error": str(e), "expect_facts": cell["expect_facts"],
                                       "expect_answer": cell["expect_answer"], "label": cell["label"]}

        # 結果矩陣
        print("\n═════════════ 結果矩陣 ═════════════")
        print(f"{'cell':<14}  {'facts':<8}  {'answer':<10}  expect_facts  expect_answer")
        print("─" * 80)
        for cid, info in results.items():
            if "error" in info:
                print(f"{cid:<14}  ERROR")
                continue
            f = info["facts"]; a = info["answer"]
            icon = {"correct": "✅", "both": "🟡", "wrong": "❌", "none": "❌"}[a["verdict"]]
            print(f"{cid:<14}  {f['score']}/{f['total']}    "
                  f"{icon} {a['verdict']:<7}  {info['expect_facts']}/3          {info['expect_answer']}")

        # 寫 results.json
        out_json = {cid: ({k: v for k, v in info.items() if k != "facts" and k != "answer"} |
                          ({"facts": info["facts"], "answer": info["answer"]} if "facts" in info else {}))
                    for cid, info in results.items()}
        with open(os.path.join(RUN, "results.json"), "w", encoding="utf-8") as f:
            json.dump(out_json, f, ensure_ascii=False, indent=2)
        print(f"\n📄 詳細結果：{os.path.join(RUN, 'results.json')}")

    finally:
        kill_procs(procs)
        try: pr.terminate()
        except Exception: pass


if __name__ == "__main__":
    main()
