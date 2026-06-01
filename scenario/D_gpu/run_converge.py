#!/usr/bin/env python3
"""
智慧路由「收斂」量測 — 證明 reputation 學習讓 fan-out 下降但仍找得到答案。

寬拓樸（讓 flood vs targeted 差距明顯）：
  Bob 直接信任 4 人：Alice / Carol / Erin / Frank（後三人 + Alice 對此題都無關）
  只有 Carol 通往答案：Carol → Dave，Dave 持有「CUDA 11.4 / Above 4G」。
題目是「驅動專屬」→ 只有 Dave 答得出；答案沿 Bob←Carol←Dave 回來 → Bob 學到「cuda 這題往 Carol 轉」。

連跑數次（每次新 Bob 進程，重讀 bob 的 agents.json 累積的 reputation）：
  run1 冷啟動 → flood 4；run2+ → targeted（Carol + 1 探索）≈ 2。fan-out 下降、答案仍命中。

跑法：.venv/bin/python scenario/D_gpu/run_converge.py [--runs 3] [--model ...]
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_scenario as rs                       # 重用 PY/P2P/RELAY/RELAY_PORT
sys.path.insert(0, rs.ROOT)
import e2ee        # noqa: E402
import agents      # noqa: E402
import app_layer   # noqa: E402

RUN = os.path.join(HERE, "run_conv")
PORTS = {"bob": 8850, "alice": 8851, "carol": 8852, "erin": 8853, "frank": 8854, "dave": 8855}
GOAL = "我要在機房那台 NVIDIA K40 顯卡的機器上跑服務，驅動要鎖哪個 CUDA 版本？開機 BIOS 要關掉什麼設定？"

ALL = ("bob", "alice", "carol", "erin", "frank", "dave")
# 只有 Dave 對這題有資料；Alice/Erin/Frank 對此題「沒有相關資料」(vault 空) → 不會作答、不被 credit。
# Carol 是純中繼(自己 vault 空、但會把 query 轉給 Dave)。
# 用「空 vault」而非「離題文字」是為了與模型無關地確定誘餌不作答(程式碼有 `cap.relevant and own` 雙重把關)。
SEEDS = {
    "dave": "踩雷紀錄：機房那批是 NVIDIA K40，驅動只能鎖 CUDA 11.4（11.8 也會掛）；開機前 BIOS 一定要關 Above 4G Decoding，否則開不了機。",
}


def keyf(w): return os.path.join(RUN, w, "node.key")
def agf(w):  return os.path.join(RUN, w, "agents.json")
def shf(w):  return os.path.join(RUN, w, "share")


def setup():
    import shutil
    shutil.rmtree(RUN, ignore_errors=True)
    pub = {}
    for w in ALL:
        app_layer.ensure_share(shf(w))
        if w in SEEDS:                                  # 只有 Dave 有資料；其餘 vault 空
            with open(os.path.join(shf(w), "read-only", "note.md"), "w", encoding="utf-8") as f:
                f.write(SEEDS[w])
        pub[w] = e2ee.public_hex(e2ee.load_or_create_identity(keyf(w)))
    # 稀疏金鑰圖：Bob→{Alice,Carol,Erin,Frank}；Carol→{Bob,Dave}；其餘回程信任
    agents.add(pub["alice"], "Alice", agf("bob"))
    agents.add(pub["carol"], "Carol", agf("bob"))
    agents.add(pub["erin"],  "Erin",  agf("bob"))
    agents.add(pub["frank"], "Frank", agf("bob"))
    for w in ("alice", "carol", "erin", "frank"):
        agents.add(pub["bob"], "Bob", agf(w))     # 回程：他們信任 Bob
    agents.add(pub["dave"], "Dave", agf("carol"))  # 只有 Carol 認識 Dave
    agents.add(pub["carol"], "Carol", agf("dave"))
    return pub


def start_node(w):
    logf = open(os.path.join(RUN, f"{w}.log"), "a")
    return subprocess.Popen(
        [rs.PY, "-u", rs.P2P, "--port", str(PORTS[w]), "--name", w.capitalize(),
         "--key-file", keyf(w), "--agents-file", agf(w), "--share", shf(w),
         "--server-ip", "127.0.0.1", "--server-port", str(rs.RELAY_PORT)],
        stdout=logf, stderr=subprocess.STDOUT)


def run_bob_route(ttl=2):
    cmd = [rs.PY, "-u", rs.P2P, "--port", str(PORTS["bob"]), "--name", "Bob",
           "--key-file", keyf("bob"), "--agents-file", agf("bob"), "--share", shf("bob"),
           "--server-ip", "127.0.0.1", "--server-port", str(rs.RELAY_PORT),
           "--route", "--goal", GOAL, "--ttl", str(ttl), "--window", "200"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=900).stdout
    fanout = mode = None
    m = re.search(r"origin 發 qid=\S+ ttl=\d+ 給 (\d+)/(\d+) 個直接朋友（([^）]+)）", out)
    if m:
        fanout, mode = int(m.group(1)), m.group(3)
    idx = out.rfind("最終整理")
    summary = out[idx:] if idx >= 0 else out
    hit = ("11.4" in summary) or ("above 4g" in summary.lower())
    return fanout, mode, hit, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    if args.model:
        os.environ["LINKEDOUT_MODEL"] = args.model
    if not rs.ollama_up():
        print("❌ ollama 沒在跑"); sys.exit(1)

    pub = setup()
    print(f"🤝 拓樸：Bob→{{Alice,Carol,Erin,Frank}}；Carol→Dave（只有 Dave 懂這題）")
    print(f"🎯 {GOAL}\n")

    procs = [subprocess.Popen([rs.PY, "-u", rs.RELAY, "--port", str(rs.RELAY_PORT)],
             stdout=open(os.path.join(RUN, "relay.log"), "w"), stderr=subprocess.STDOUT)]
    time.sleep(1)
    try:
        for w in ("alice", "carol", "erin", "frank", "dave"):
            procs.append(start_node(w))
        for w in ("alice", "carol", "erin", "frank", "dave"):
            rs.wait_for(os.path.join(RUN, f"{w}.log"), "Registered as", timeout=20)
        print("🟢 5 個收訊方就緒\n")

        rows = []
        for i in range(1, args.runs + 1):
            fan, mode, hit, _ = run_bob_route()
            rows.append((i, fan, mode, hit))
            print(f"  run {i}: origin fan-out={fan}  mode={mode}  答案命中CUDA11.4={'✅' if hit else '❌'}")
            time.sleep(1)

        print("\n════════ 收斂結果 ════════")
        print(f"  {'run':<4}{'fan-out':<9}{'mode':<18}{'命中'}")
        for i, fan, mode, hit in rows:
            print(f"  {i:<4}{str(fan):<9}{str(mode):<18}{'✅' if hit else '❌'}")
        print("\n敘事：fan-out 應從 4（cold-flood）降到 ~2（targeted+explore），且每次仍命中 →")
        print("      路由表學會『這題往 Carol 轉』，訊息變少但答案照樣拿得到。")
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
