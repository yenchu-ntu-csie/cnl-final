#!/usr/bin/env python3
"""
scenario D — 跨領域專業 testbed（可攜的 orchestrator，純 Python，不依賴特定 shell）

4 個 agent，知識分散 + 金鑰圖稀疏：
    Bob(問,app) 有 Alice、Carol 金鑰；Carol 有 Dave 金鑰；Bob 沒有 Dave。
量「協作到底有沒有幫助」：
    baseline = Bob 只用自己 vault（不問人）
    v1       = Bob capability-scan + 問直接朋友 Alice/Carol（Felicity 的 --auto 群組）
預期：baseline 0-1/3、v1 2/3（搆不到 Dave 的驅動那條）。缺的那條 = 需要多跳(S4) 的證據。

用法（在 repo 根目錄已建 .venv 的前提下）：
    .venv/bin/python scenario/D_gpu/run_scenario.py
需要：ollama daemon 在跑。
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
from score import load_facts, score as score_text  # noqa: E402

RUN = os.path.join(HERE, "run")
RELAY_PORT = 9700
PORTS = {"alice": 8811, "carol": 8812, "dave": 8813, "bob": 8810}
AGENTS = ("bob", "alice", "carol", "dave")
PY = sys.executable                    # 用「正在跑這支腳本的 python」→ 可攜
P2P = os.path.join(ROOT, "p2p_node.py")
RELAY = os.path.join(ROOT, "relay_server.py")


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
    """每個 agent：建 share 四區 + 複製種子 + 產生金鑰；回傳 {who: pubkey}。"""
    import shutil
    sh_rmtree(RUN)
    pub = {}
    for who in AGENTS:
        share = os.path.join(RUN, who, "share")
        app_layer.ensure_share(share)
        seed = os.path.join(HERE, "seeds", who)
        if os.path.isdir(seed):
            for zone in os.listdir(seed):                       # e.g. read-only/
                src, dst = os.path.join(seed, zone), os.path.join(share, zone)
                if os.path.isdir(src):
                    os.makedirs(dst, exist_ok=True)
                    for fn in os.listdir(src):
                        shutil.copy2(os.path.join(src, fn), os.path.join(dst, fn))
        key = os.path.join(RUN, who, "node.key")
        pub[who] = e2ee.public_hex(e2ee.load_or_create_identity(key))
    return pub


def build_key_graph(pub):
    """稀疏金鑰圖：誰有誰的公鑰 = 誰能定址誰。"""
    def agfile(who):
        return os.path.join(RUN, who, "agents.json")
    agents.add(pub["alice"], "Alice", agfile("bob"))
    agents.add(pub["carol"], "Carol", agfile("bob"))     # Bob 沒有 Dave！
    agents.add(pub["bob"],   "Bob",   agfile("alice"))
    agents.add(pub["bob"],   "Bob",   agfile("carol"))
    agents.add(pub["dave"],  "Dave",  agfile("carol"))   # 只有 Carol 認識 Dave
    agents.add(pub["carol"], "Carol", agfile("dave"))


def start_node(who, args_extra):
    """起一個節點（subprocess），stdout 導到 run/<who>.log。回傳 (proc, logfile)。"""
    logf = open(os.path.join(RUN, f"{who}.log"), "w")
    cmd = [PY, "-u", P2P, "--port", str(PORTS[who]), "--name", who.capitalize(),
           "--key-file", os.path.join(RUN, who, "node.key"),
           "--agents-file", os.path.join(RUN, who, "agents.json"),
           "--share", os.path.join(RUN, who, "share"),
           "--server-ip", "127.0.0.1", "--server-port", str(RELAY_PORT)] + args_extra
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


def run_baseline(goal, bob_share):
    """Bob 只用自己 vault 回答（in-process，不經網路）。"""
    own = app_layer._collect_ask_context(bob_share, "personal")
    ctx = "\n\n".join(own) if own else "(Bob 自己的 vault 沒有相關筆記)"
    messages = [
        {"role": "system", "content":
            "You are Bob's local agent. Answer Bob's goal using ONLY the context below "
            "(Bob's own notes) plus general knowledge. Do NOT invent machine-specific "
            "specifics the notes don't mention. Be concrete and concise."},
        {"role": "user", "content": f"<<<CTX Bob's own notes>>>\n{ctx}\n<<<END CTX>>>\n\nGoal: {goal}"},
    ]
    return ai_client._call_sync(None, messages)


def run_v1(goal, pub):
    """Bob --auto 群組：capability scan + 問直接朋友 Alice/Carol。回傳 (最終答案, 完整log)。"""
    cmd = [PY, "-u", P2P, "--port", str(PORTS["bob"]), "--name", "Bob",
           "--key-file", os.path.join(RUN, "bob", "node.key"),
           "--agents-file", os.path.join(RUN, "bob", "agents.json"),
           "--share", os.path.join(RUN, "bob", "share"),
           "--server-ip", "127.0.0.1", "--server-port", str(RELAY_PORT),
           "--peer-pubkey", f"{pub['alice']},{pub['carol']}",
           "--auto", "--goal", goal, "--rounds", "3"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    out = res.stdout + res.stderr
    # 取「最終整理」之後的文字當答案
    marker = "最終整理"
    idx = out.rfind(marker)
    summary = out[idx + len(marker):].lstrip("：:\n ") if idx >= 0 else out
    return summary, out


def show_score(label, text):
    facts = load_facts()
    res = score_text(text, facts)
    print(f"── [{label}] score ──")
    for h in res["hits"]:
        mark = "✅" if h["hit"] else "❌"
        info = f"（命中：{h['via']}）" if h["hit"] else f"（缺，這條在 {h['owner']} 手上）"
        print(f"   {mark} {h['id']:<14} {info}")
    print(f"   SCORE: {res['score']}/{res['total']}")
    return res


def main():
    if not ollama_up():
        print("❌ ollama daemon 沒在跑（http://localhost:11434）。先 `ollama serve` 並確認有模型。")
        sys.exit(1)

    goal = json.load(open(os.path.join(HERE, "ground_truth.json"), encoding="utf-8"))["goal"]
    print("🎯 goal：", goal, "\n")

    pub = setup_workdir()
    build_key_graph(pub)
    print(f"🔑 pubkeys：" + "  ".join(f"{w}={pub[w][:8]}…" for w in AGENTS))
    print("🤝 金鑰圖：Bob→{Alice,Carol}；Carol→{Bob,Dave}；Dave→{Carol}（Bob 搆不到 Dave）\n")

    procs = []
    try:
        # relay + 三個收訊方
        pr = subprocess.Popen([PY, "-u", RELAY, "--port", str(RELAY_PORT)],
                              stdout=open(os.path.join(RUN, "relay.log"), "w"),
                              stderr=subprocess.STDOUT)
        procs.append(pr)
        time.sleep(1)
        for who in ("alice", "carol", "dave"):
            p, _ = start_node(who, [])
            procs.append(p)
        for who in ("alice", "carol", "dave"):
            ok = wait_for(os.path.join(RUN, f"{who}.log"), "Registered as", timeout=20)
            print(f"   {'🟢' if ok else '🔴'} {who} {'registered' if ok else 'NOT registered'}")
        print()

        # baseline
        print("════════ baseline（Bob 只用自己 vault，不問人）════════")
        base = run_baseline(goal, os.path.join(RUN, "bob", "share"))
        for line in base.splitlines()[:10]:
            print("   │", line)
        base_res = show_score("baseline", base)
        print()

        # v1
        print("════════ v1（Bob --auto 群組：capability scan + 問 Alice/Carol）════════")
        summary, full = run_v1(goal, pub)
        for line in full.splitlines():
            if any(k in line for k in ("capability scan 完成", "→ ", "[Auto/r")):
                print("  ", line.strip())
        print("   ── v1 最終答案 ──")
        for line in summary.splitlines()[:12]:
            print("   │", line)
        v1_res = show_score("v1", summary)
        print()

        # 對照
        print("════════ 對照 ════════")
        print(f"   baseline : {base_res['score']}/{base_res['total']}")
        print(f"   v1       : {v1_res['score']}/{v1_res['total']}")
        print("\n預期：baseline 低、v1=2/3（拿到 Alice 量化 + Carol 遠端；缺 Dave 驅動，因 Bob 搆不到 Dave）。")
        print("→ 缺的那 1/3 = 「需要多跳(S4) 讓 Carol 代轉到 Dave」的可量化證據。")
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
