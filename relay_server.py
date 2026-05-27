import asyncio
import json
import socket
import argparse
from typing import Dict

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
                        err = json.dumps({"type": "ERROR", "reason": "peer_offline"}) + "\n"
                        writer.write(err.encode())
                        await writer.drain()
                        print(f"   ⚠️  {to} offline")

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
