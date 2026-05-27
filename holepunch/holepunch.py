"""
UDP 打洞可行性測試 —— Client。

流程（不用 IPv6 的 P2P 核心）：
    1. 用一個 UDP socket 向 rendezvous 登記（server 藉此看到我的公網 UDP 位址）
    2. 拿到對方的公網位址
    3. 用「同一個」socket 不斷往對方公網位址送 PUNCH（打洞）
       - 我送出 → 我的 NAT 開一個「允許對方回來」的洞
       - 對方同時送 → 兩個洞對上 → 直連成立
    4. 收到對方封包 = 洞通了；互傳一筆 DATA 確認雙向

判讀：
    ✅ 直連成功 → 你們的 NAT 可打洞，值得把 UDP P2P 整合進主程式
    ❌ 打洞失敗 → 多半是 symmetric NAT，這條路走不通，得用 relay 中繼或 UPnP

用法（兩台機器各跑一個，server 填 rendezvous 機器 IP）：
    # 機器 A
    python3 holepunch.py --name A --peer B --server 140.112.30.183 --server-port 9999
    # 機器 B
    python3 holepunch.py --name B --peer A --server 140.112.30.183 --server-port 9999
"""

import argparse
import json
import socket
import time


def send(sock, obj, addr):
    sock.sendto(json.dumps(obj).encode(), addr)


def main(a):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", a.port))
    local_port = sock.getsockname()[1]
    server = (a.server, a.server_port)
    print(f"🧩 我是 [{a.name}]，本機 UDP port={local_port}，要找 [{a.peer}]")

    # 1. 登記（用同一個 socket 出去，server 看到的就是我真正的公網 UDP 映射）
    send(sock, {"name": a.name, "peer": a.peer}, server)

    # 2. 等 rendezvous 告知對方公網位址
    sock.settimeout(a.wait)
    peer_addr = None
    while peer_addr is None:
        try:
            data, _ = sock.recvfrom(4096)
            msg = json.loads(data.decode())
            if "addr" in msg:
                peer_addr = tuple(msg["addr"])
                print(f"📍 對方 [{a.peer}] 公網位址 = {peer_addr[0]}:{peer_addr[1]}")
        except socket.timeout:
            print("❌ 等不到對方位址（對方還沒上線？rendezvous 通嗎？）")
            return

    # 3. 打洞：雙方同時往對方公網位址狂送 PUNCH
    print("🔨 開始打洞（每 0.3s 送一次 PUNCH）…")
    sock.settimeout(0.3)
    got_from_peer = False   # 收到對方封包 → 我的洞開了（對方進得來）
    peer_got_mine = False   # 收到對方 ACK → 我送得到對方
    deadline = time.time() + a.timeout
    while time.time() < deadline and not (got_from_peer and peer_got_mine):
        send(sock, {"t": "PUNCH", "from": a.name}, peer_addr)
        try:
            data, addr = sock.recvfrom(4096)
            m = json.loads(data.decode())
            if m.get("t") == "PUNCH":
                if not got_from_peer:
                    print(f"   ⬅️  收到對方 PUNCH（洞通了）")
                got_from_peer = True
                send(sock, {"t": "ACK", "from": a.name}, addr)
            elif m.get("t") == "ACK":
                peer_got_mine = True
        except socket.timeout:
            pass

    # 4. 結果判讀
    if got_from_peer and peer_got_mine:
        print(f"✅ 直連成功！P2P UDP 通道建立（{peer_addr[0]}:{peer_addr[1]}）")
        for _ in range(3):   # 多送幾次，蓋過還在飛的 PUNCH/ACK
            send(sock, {"t": "DATA", "msg": f"hello from {a.name} 🎉"}, peer_addr)
        sock.settimeout(3)
        deadline2 = time.time() + 3
        while time.time() < deadline2:   # 過濾掉殘留的 PUNCH/ACK，只認 DATA
            try:
                data, _ = sock.recvfrom(4096)
                m = json.loads(data.decode())
                if m.get("t") == "DATA":
                    print(f"📨 收到對方資料：{m.get('msg')}")
                    break
            except socket.timeout:
                print("（沒收到對方 DATA，但打洞已成功）")
                break
        print("👉 結論：你們的 NAT 可以打洞，值得把 UDP P2P 整合進主程式。")
    else:
        print("❌ 打洞失敗（got_from_peer=%s, peer_got_mine=%s）" % (got_from_peer, peer_got_mine))
        print("👉 結論：多半是 symmetric NAT，這條路走不通；改走 relay 中繼或試 UPnP。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="UDP hole-punch client (feasibility test)")
    ap.add_argument("--name", required=True, help="我的代號（例：A）")
    ap.add_argument("--peer", required=True, help="對方代號（例：B）")
    ap.add_argument("--server", required=True, help="rendezvous 伺服器 IP")
    ap.add_argument("--server-port", type=int, default=9999)
    ap.add_argument("--port", type=int, default=0, help="本機 UDP port（0=自動）")
    ap.add_argument("--wait", type=int, default=30, help="等對方上線的秒數")
    ap.add_argument("--timeout", type=int, default=20, help="打洞嘗試秒數")
    args = ap.parse_args()
    try:
        main(args)
    except KeyboardInterrupt:
        print("\n👋 stopped.")
