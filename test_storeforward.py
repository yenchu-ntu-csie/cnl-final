#!/usr/bin/env python3
"""
relay 離線存轉(store-and-forward)整合測試 — 不需 ollama（用 list op）。

劇本：
  1. 起 relay。
  2. Carol 上線（註冊）後關掉 = 離線。
  3. Bob 在 Carol 離線時送一個 list 請求 → relay 應「暫存」而非丟棄，Bob 應收到 QUEUED。
  4. Carol 重新上線 → relay 補投 → Carol 的 log 應出現收到那個 REQUEST(list)。

跑法：.venv/bin/python test_storeforward.py
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import e2ee        # noqa: E402
import agents      # noqa: E402
import app_layer   # noqa: E402

PY = sys.executable
RELAY_PORT = 9710
WORK = tempfile.mkdtemp(prefix="sf_")


def keyfile(who):
    return os.path.join(WORK, f"{who}.key")


def agfile(who):
    return os.path.join(WORK, f"{who}_agents.json")


def share(who):
    d = os.path.join(WORK, who, "share")
    app_layer.ensure_share(d)
    return d


def start_relay():
    return subprocess.Popen([PY, "-u", os.path.join(ROOT, "relay_server.py"), "--port", str(RELAY_PORT)],
                            stdout=open(os.path.join(WORK, "relay.log"), "w"), stderr=subprocess.STDOUT)


def start_carol():
    return subprocess.Popen(
        [PY, "-u", os.path.join(ROOT, "p2p_node.py"), "--port", "8821", "--name", "Carol",
         "--key-file", keyfile("carol"), "--agents-file", agfile("carol"), "--share", share("carol"),
         "--server-ip", "127.0.0.1", "--server-port", str(RELAY_PORT)],
        stdout=open(os.path.join(WORK, "carol.log"), "a"), stderr=subprocess.STDOUT)


def wait_log(path, needle, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path) and needle in open(path, encoding="utf-8", errors="ignore").read():
            return True
        time.sleep(0.3)
    return False


def main():
    # keys + 互信
    bob = e2ee.public_hex(e2ee.load_or_create_identity(keyfile("bob")))
    carol = e2ee.public_hex(e2ee.load_or_create_identity(keyfile("carol")))
    agents.add(carol, "Carol", agfile("bob"))
    agents.add(bob, "Bob", agfile("carol"))
    # Carol 的 share 放一個檔（list 看得到）
    with open(os.path.join(share("carol"), "read-only", "n.md"), "w", encoding="utf-8") as f:
        f.write("hello from carol")

    procs = []
    try:
        procs.append(start_relay()); time.sleep(1)

        # 1) Carol 上線後關掉 → 離線
        c1 = start_carol(); procs.append(c1)
        assert wait_log(os.path.join(WORK, "carol.log"), "Registered as"), "Carol 第一次沒註冊上"
        c1.terminate(); c1.wait(); time.sleep(1)
        print("✅ Carol 已離線")

        # 2) Bob 在 Carol 離線時送 list（one-shot；幾秒後關掉）
        bobp = subprocess.Popen(
            [PY, "-u", os.path.join(ROOT, "p2p_node.py"), "--port", "8820", "--name", "Bob",
             "--key-file", keyfile("bob"), "--agents-file", agfile("bob"), "--share", share("bob"),
             "--server-ip", "127.0.0.1", "--server-port", str(RELAY_PORT),
             "--peer-pubkey", carol, "--op", "list"],
            stdout=open(os.path.join(WORK, "bob.log"), "w"), stderr=subprocess.STDOUT)
        procs.append(bobp)
        got_queued = wait_log(os.path.join(WORK, "bob.log"), "QUEUED", timeout=10) or \
                     wait_log(os.path.join(WORK, "bob.log"), "暫存", timeout=1)
        bobp.terminate()
        assert got_queued, "Bob 沒收到 QUEUED（離線訊息應被暫存而非丟棄）\n" + \
            open(os.path.join(WORK, "bob.log")).read()
        print("✅ 對方離線 → relay 暫存（Bob 收到 QUEUED，非 ERROR/丟棄）")

        # 3) Carol 重新上線 → 應收到補投的 REQUEST(list)
        # 先記住目前 carol.log 長度，之後只看新內容
        carol_log = os.path.join(WORK, "carol.log")
        before = os.path.getsize(carol_log) if os.path.exists(carol_log) else 0
        c2 = start_carol(); procs.append(c2)
        assert wait_log(carol_log, "Registered as", timeout=15), "Carol 第二次沒註冊上"
        # 補投的 REQUEST 應在重新上線後出現
        ok = False
        end = time.time() + 12
        while time.time() < end:
            tail = open(carol_log, encoding="utf-8", errors="ignore").read()[before:]
            if "op=list" in tail or "[Request]" in tail or "Received" in tail.replace("\n", " "):
                ok = True; break
            time.sleep(0.3)
        assert ok, "Carol 重新上線後沒收到被暫存的 REQUEST\n" + open(carol_log).read()[before:]
        print("✅ Carol 重新上線 → relay 補投暫存封包，Carol 收到 REQUEST(list)")
        print("\n🎉 store-and-forward 測試通過")
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
