"""
UDP 打洞可行性測試 —— Rendezvous（信令）伺服器。

跑在一台兩邊都連得到的機器上（例如你們的 relay 機 140.112.30.183）。
它的工作：用同一條 UDP 看到每個 client 的「公網 UDP 位址 (ip:port)」，
等互相指名的兩個 client 都登記後，把對方的位址告訴彼此（= STUN + 牽線）。

這支只負責「交換位址」，不轉發資料；資料是兩個 client 直接打洞傳。

用法：
    python3 udp_rendezvous.py --port 9999
"""

import argparse
import json
import socket


def main(port: int):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    print(f"🟢 [Rendezvous] UDP listening on :{port}")

    reg = {}  # name -> (addr, wanted_peer)
    while True:
        data, addr = s.recvfrom(4096)
        try:
            msg = json.loads(data.decode())
        except Exception:
            continue
        name, peer = msg.get("name"), msg.get("peer")
        if not name:
            continue

        reg[name] = (addr, peer)
        print(f"📥 [{name}] @ {addr[0]}:{addr[1]}　想找 [{peer}]")

        # 雙方互相指名、且都已登記 → 互換公網位址
        if peer in reg and reg[peer][1] == name:
            peer_addr = reg[peer][0]
            s.sendto(json.dumps({"peer_name": peer, "addr": list(peer_addr)}).encode(), addr)
            s.sendto(json.dumps({"peer_name": name, "addr": list(addr)}).encode(), peer_addr)
            print(f"🤝 [介紹] {name} ({addr[0]}:{addr[1]})  <->  {peer} ({peer_addr[0]}:{peer_addr[1]})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="UDP hole-punch rendezvous server")
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    try:
        main(args.port)
    except KeyboardInterrupt:
        print("\n👋 rendezvous stopped.")
