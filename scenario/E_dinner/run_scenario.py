#!/usr/bin/env python3
"""
scenario E — 規劃聚餐：飲食限制 × 親密度 testbed

故事：Bob 想揪 Alice / Carol / Dave 聚餐，希望不踩到任何人的飲食限制。
三筆 fact 對應三個 tier：
  • Alice 的「日式料理 + 1500 預算」放 read-only/    （common 看得到）
  • Carol 的「減肥不吃油炸」          放 task/        （task 才看得到）
  • Dave  的「海鮮嚴重過敏」          放 personal/    （personal 才看得到）

變數：Alice/Carol/Dave 各自對 **Bob** 設的 tier。4 個有意義的組合：
  A. all_common                    → 預期 1/3
  B. carol_task_only               → 預期 2/3（+Carol）
  C. dave_personal_only            → 預期 2/3（+Dave，但缺 Carol）
  D. carol_task_dave_personal      → 預期 3/3（完美）

衡量：Bob `--auto` 跑完後的「最終整理」字串，命中多少 ground_truth.facts。

用法（repo 根目錄 .venv 已建、ollama 在跑）：
    .venv/bin/python scenario/E_dinner/run_scenario.py
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
from score import load_facts, score as score_text  # noqa: E402

RUN = os.path.join(HERE, "run")
RELAY_PORT = 9710                          # 跟 D 的 9700 錯開、避免互卡
PORTS = {"alice": 8821, "carol": 8822, "dave": 8823, "bob": 8820}
PEERS = ("alice", "carol", "dave")
AGENTS = ("bob",) + PEERS
PY = sys.executable
P2P = os.path.join(ROOT, "p2p_node.py")
RELAY = os.path.join(ROOT, "relay_server.py")

# 4 個實驗 cell：每個 peer 對 Bob 設什麼 tier
CELLS = [
    {"id": "A_all_common",               "label": "全 common（Bob 跟誰都不熟）",
     "tiers": {"alice": "common", "carol": "common", "dave": "common"},
     "expect": 1},
    {"id": "B_carol_task",               "label": "Carol→task（Bob 跟 Carol 是同事）",
     "tiers": {"alice": "common", "carol": "task",   "dave": "common"},
     "expect": 2},
    {"id": "C_dave_personal",            "label": "Dave→personal（Bob 跟 Dave 是密友）",
     "tiers": {"alice": "common", "carol": "common", "dave": "personal"},
     "expect": 2},
    {"id": "D_carol_task_dave_personal", "label": "Carol→task + Dave→personal（雙線解鎖）",
     "tiers": {"alice": "common", "carol": "task",   "dave": "personal"},
     "expect": 3},
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
    """建工作目錄 + 複製種子 + 產金鑰，回傳 {who: pubkey}。"""
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


def write_agents_json(pub, cell):
    """根據 cell 的 tier 設定，寫每個 peer 的 agents.json。
       Bob 永遠把 Alice/Carol/Dave 加在白名單（tier 不重要：受訊端的 tier 才有用）。"""
    bob_agents = os.path.join(RUN, "bob", "agents.json")
    # Bob 的 agents.json：他要能定址三人
    if os.path.exists(bob_agents):
        os.remove(bob_agents)
    for p in PEERS:
        agents.add(pub[p], p.capitalize(), bob_agents)

    # Alice / Carol / Dave 各自的 agents.json：把 Bob 設成 cell 指定的 tier
    for p in PEERS:
        agf = os.path.join(RUN, p, "agents.json")
        if os.path.exists(agf):
            os.remove(agf)
        agents.add(pub["bob"], "Bob", agf, tier=cell["tiers"][p])


def start_peer(who):
    """起一個 peer 節點（subprocess），stdout 導到 run/<who>.log。"""
    logf = open(os.path.join(RUN, f"{who}.log"), "w")
    cmd = [PY, "-u", P2P, "--port", str(PORTS[who]), "--name", who.capitalize(),
           "--key-file", os.path.join(RUN, who, "node.key"),
           "--agents-file", os.path.join(RUN, who, "agents.json"),
           "--share", os.path.join(RUN, who, "share"),
           "--server-ip", "127.0.0.1", "--server-port", str(RELAY_PORT)]
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


def run_bob_auto(goal, pub, cell_id):
    """Bob 跑 --auto 群組討論：capability scan + 問 Alice/Carol/Dave 三人。"""
    cmd = [PY, "-u", P2P, "--port", str(PORTS["bob"]), "--name", "Bob",
           "--key-file", os.path.join(RUN, "bob", "node.key"),
           "--agents-file", os.path.join(RUN, "bob", "agents.json"),
           "--share", os.path.join(RUN, "bob", "share"),
           "--server-ip", "127.0.0.1", "--server-port", str(RELAY_PORT),
           "--peer-pubkey", f"{pub['alice']},{pub['carol']},{pub['dave']}",
           "--auto", "--goal", goal, "--rounds", "3"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    out = res.stdout + res.stderr
    # 存 log
    with open(os.path.join(RUN, f"bob_{cell_id}.log"), "w", encoding="utf-8") as f:
        f.write(out)
    marker = "最終整理"
    idx = out.rfind(marker)
    summary = out[idx + len(marker):].lstrip("：:\n ") if idx >= 0 else out
    return summary, out


def kill_procs(procs):
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(0.5)
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass


def show_score(label, text):
    facts = load_facts()
    res = score_text(text, facts)
    print(f"── [{label}] score ──")
    for h in res["hits"]:
        mark = "✅" if h["hit"] else "❌"
        info = f"（命中：{h['via']}）" if h["hit"] else f"（缺，這條在 {h['owner']} 的 {next((f['zone'] for f in facts if f['id']==h['id']), '?')} 區）"
        print(f"   {mark} {h['id']:<14} {info}")
    print(f"   SCORE: {res['score']}/{res['total']}")
    return res


def print_heatmap(results):
    """把 4 個 cell 的結果印成簡易表格。"""
    facts = load_facts()
    print("\n════════════ 結果矩陣 ════════════")
    headers = ["cell"] + [f["id"] for f in facts] + ["score", "expect"]
    print(f"{'cell':<32}  " + " ".join(f"{h:<14}" for h in headers[1:-2]) +
          f"  {'score':<6}  {'expect':<6}")
    print("─" * 110)
    for cid, info in results.items():
        cells_v = [("✅" if h["hit"] else "❌") for h in info["hits"]]
        print(f"{cid:<32}  " + " ".join(f"{v:<14}" for v in cells_v) +
              f"  {info['score']}/{info['total']}    {info['expect']}/3")
    print()


def main():
    if not ollama_up():
        print("❌ ollama daemon 沒在跑（http://localhost:11434）。先 `ollama serve`。")
        sys.exit(1)

    gt = json.load(open(os.path.join(HERE, "ground_truth.json"), encoding="utf-8"))
    goal = gt["goal"]
    print("🎯 goal：", goal[:80], "...\n")

    pub = setup_workdir()
    print("🔑 pubkeys：" + "  ".join(f"{w}={pub[w][:8]}…" for w in AGENTS))
    print()

    # 起 relay（4 個 cell 共用）
    pr = subprocess.Popen([PY, "-u", RELAY, "--port", str(RELAY_PORT)],
                          stdout=open(os.path.join(RUN, "relay.log"), "w"),
                          stderr=subprocess.STDOUT)
    time.sleep(1.5)

    results = {}
    try:
        for cell in CELLS:
            print(f"\n════════ Cell [{cell['id']}] {cell['label']} ════════")
            print(f"   tier 設定：" + "  ".join(f"{p}→Bob={t}" for p, t in cell["tiers"].items()))

            # 為這個 cell 重寫 agents.json（會改 tier）
            write_agents_json(pub, cell)

            # 為這個 cell 重啟三個 peer（agents.json 在啟動時讀進記憶體）
            procs = []
            for who in PEERS:
                p, _ = start_peer(who)
                procs.append(p)
            for who in PEERS:
                ok = wait_for(os.path.join(RUN, f"{who}.log"), "Registered as", timeout=20)
                if not ok:
                    print(f"   🔴 {who} 沒上線 → 跳過這個 cell")
            time.sleep(1)

            # 跑 Bob 的 --auto
            try:
                summary, _full = run_bob_auto(goal, pub, cell["id"])
                # 印幾行供 debug
                for line in summary.splitlines()[:8]:
                    print("   │", line)
                r = show_score(cell["id"], summary)
                results[cell["id"]] = {**r, "expect": cell["expect"], "label": cell["label"]}
            except Exception as e:
                print(f"   ❌ Bob run failed: {e}")
                results[cell["id"]] = {"score": 0, "total": 3, "hits": [],
                                       "expect": cell["expect"], "label": cell["label"]}
            finally:
                kill_procs(procs)
                time.sleep(1)

        # 結果摘要
        print_heatmap(results)
        # 寫 JSON 給後續分析用
        out_json = {cid: {k: v for k, v in info.items() if k != "hits"} | {"hits": info.get("hits", [])}
                    for cid, info in results.items()}
        with open(os.path.join(RUN, "results.json"), "w", encoding="utf-8") as f:
            json.dump(out_json, f, ensure_ascii=False, indent=2)
        print(f"📄 詳細結果：{os.path.join(RUN, 'results.json')}")
    finally:
        try:
            pr.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()
