import asyncio
import json
import socket
import argparse
from typing import Dict, List

# 離線存轉：每個 pubkey 最多暫存幾則（防無限增長；超過丟最舊的）
MAX_QUEUE_PER_PEER = 50

def get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()

class RelayServer:
    """
    資料轉發 Server（Relay）。
    兩個 node 都連上來之後，封包由此 server 轉發。
    加密是 end-to-end，server 只負責路由，無法解讀內容。
    """
    def __init__(self, port: int = 9000):
        self.port = port
        self.peers: Dict[str, asyncio.StreamWriter] = {}  # pubkey → writer
        self.queues: Dict[str, List[dict]] = {}           # 離線存轉：pubkey → 待投遞封包

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        pubkey = None
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break

                msg = json.loads(data.decode().strip())

                # 節點登記
                if msg["type"] == "REGISTER":
                    pubkey = msg["pubkey"]
                    self.peers[pubkey] = writer
                    print(f"✅ [Register] {pubkey}")

                    ack = json.dumps({"type": "ACK", "status": "registered"}) + "\n"
                    writer.write(ack.encode())
                    await writer.drain()

                    online = list(self.peers.keys())
                    info = json.dumps({"type": "PEER_LIST", "online": online}) + "\n"
                    writer.write(info.encode())
                    await writer.drain()

                    # 離線存轉：上線就把暫存的封包補投
                    queued = self.queues.pop(pubkey, [])
                    if queued:
                        print(f"   📨 [Flush] {str(pubkey)[:12]}… 上線，補投 {len(queued)} 則暫存封包")
                        for pkt in queued:
                            deliver = json.dumps({"type": "DELIVER", "packet": pkt}) + "\n"
                            writer.write(deliver.encode())
                        await writer.drain()

                # 轉發封包給目標
                elif msg["type"] == "FORWARD":
                    to = msg.get("to_pubkey")
                    if to in self.peers:
                        deliver = json.dumps({
                            "type": "DELIVER",
                            "packet": msg["packet"]
                        }) + "\n"
                        self.peers[to].write(deliver.encode())
                        await self.peers[to].drain()
                        enc = msg.get("packet", {}).get("payload", {}).get("enc")
                        seal = "🔒 payload encrypted (relay 看不懂內容)" if enc else "⚠️ plaintext payload"
                        print(f"   📦 {str(pubkey)[:12]}… → {str(to)[:12]}…  | {seal}")
                    else:
                        # 離線存轉：對方不在線 → 暫存，等他上線再補投（取代直接丟棄）
                        q = self.queues.setdefault(to, [])
                        q.append(msg["packet"])
                        if len(q) > MAX_QUEUE_PER_PEER:
                            q.pop(0)                       # 超量丟最舊的（bounded）
                        notice = json.dumps({"type": "QUEUED", "to_pubkey": to, "depth": len(q)}) + "\n"
                        writer.write(notice.encode())
                        await writer.drain()
                        print(f"   📦→📥 {str(to)[:12]}… offline，暫存（佇列 {len(q)}）")

        except (asyncio.IncompleteReadError, ConnectionResetError, json.JSONDecodeError):
            pass
        except Exception as e:
            print(f"⚠️  Error: {e}")
        finally:
            if pubkey and pubkey in self.peers:
                del self.peers[pubkey]
                print(f"🔴 [Offline] {pubkey}")
            writer.close()

    async def start(self):
        server = await asyncio.start_server(self.handle_client, "0.0.0.0", self.port)
        lan = get_lan_ip()
        print(f"🟢 [Relay] Running on {lan}:{self.port}")
        print(f"💡 Nodes connect with: --server-ip {lan} --server-port {self.port}")
        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedOut Relay Server")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    try:
        asyncio.run(RelayServer(port=args.port).start())
    except KeyboardInterrupt:
        print("\n👋 Relay stopped.")
