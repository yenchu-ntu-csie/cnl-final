import asyncio
import argparse
import json
import socket
import uuid
import time
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

# ==========================================
# 1. 資料模型 (照你們的定義，微調以適應 Pydantic v2)
# ==========================================
class PacketHeader(BaseModel):
    sender_pubkey: str
    target_pubkey: Optional[str] = None
    ttl: int = Field(default=5, ge=0)
    timestamp: int
    hops: List[str] = []

class ProtocolPacket(BaseModel):
    version: str = "1.0"
    msg_id: str
    type: str
    header: PacketHeader
    payload: Dict
    signature: str

# ==========================================
# 2. 網路層實作 (Network Layer)
# ==========================================
class P2PNode:
    def __init__(self, host: str, port: int, my_pubkey: str):
        self.host = host
        self.port = port
        self.my_pubkey = my_pubkey
        self.server = None
        self.srv_reader: Optional[asyncio.StreamReader] = None
        self.srv_writer: Optional[asyncio.StreamWriter] = None

    async def start_server(self):
        """啟動 Socket Server 監聽外來連線"""
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        print(f"🟢 [Server] Listening on {self.host}:{self.port}")
        async with self.server:
            await self.server.serve_forever()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """處理接收到的 TCP 串流"""
        addr = writer.get_extra_info('peername')
        print(f"📥 [Server] Accepted connection from {addr}")
        
        try:
            while True:
                # 使用 readline 來處理 TCP 黏包問題，以 \n 為界
                data = await reader.readline()
                if not data:
                    break # 連線關閉
                
                json_str = data.decode('utf-8').strip()
                try:
                    # 解析並驗證 JSON
                    packet = ProtocolPacket.model_validate_json(json_str)
                    print(f"   ➔ [Received] Type: {packet.type} | MsgID: {packet.msg_id[:8]}... | From: {packet.header.sender_pubkey}")
                    
                    # 這裡模擬觸發 P6 的 Callback
                    await self.mock_p6_routing(packet)
                    
                except Exception as e:
                    print(f"   ⚠️ [Error] Invalid Packet format: {e}")
                    
        except asyncio.CancelledError:
            pass
        finally:
            print(f"🔴 [Server] Connection closed from {addr}")
            writer.close()
            await writer.wait_closed()

    async def send_packet(self, peer_ip: str, peer_port: int, packet: ProtocolPacket, retries=3) -> bool:
        """主動發送封包 (帶有簡單 Retry 機制)"""
        for attempt in range(retries):
            try:
                reader, writer = await asyncio.open_connection(peer_ip, peer_port)
                
                # 序列化 JSON 並加上換行符號 \n
                json_data = packet.model_dump_json() + "\n"
                writer.write(json_data.encode('utf-8'))
                await writer.drain()
                
                print(f"📤 [Sent] Type: {packet.type} to {peer_ip}:{peer_port}")
                
                writer.close()
                await writer.wait_closed()
                return True
            
            except ConnectionRefusedError:
                print(f"   ⏳ [Retry {attempt+1}/{retries}] Connection refused by {peer_ip}:{peer_port}. Retrying in 2s...")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"   ⚠️ [Error] Failed to send to {peer_ip}:{peer_port} - {e}")
                break
        
        print(f"❌ [Failed] Could not deliver packet to {peer_ip}:{peer_port}")
        return False

    # ==========================================
    # 3. Relay 模式（封包經 server 轉發，E2EE）
    # ==========================================
    async def run_relay(self, server_ip: str, server_port: int):
        """連上 relay server，登記，並持續收發封包"""
        print(f"🔗 [Relay] Connecting to {server_ip}:{server_port} ...")
        self.srv_reader, self.srv_writer = await asyncio.open_connection(server_ip, server_port)

        # 登記
        reg = json.dumps({"type": "REGISTER", "pubkey": self.my_pubkey}) + "\n"
        self.srv_writer.write(reg.encode())
        await self.srv_writer.drain()

        # 等 ACK
        ack = json.loads((await self.srv_reader.readline()).decode().strip())
        if ack.get("type") == "ACK":
            print(f"✅ [Relay] Registered as {self.my_pubkey}")

        # 持續監聽 relay 推來的封包
        while True:
            data = await self.srv_reader.readline()
            if not data:
                print("⚠️  [Relay] Connection lost.")
                break
            msg = json.loads(data.decode().strip())

            if msg["type"] == "DELIVER":
                try:
                    packet = ProtocolPacket.model_validate(msg["packet"])
                    print(f"📥 [Relay] From: {packet.header.sender_pubkey} | Type: {packet.type}")
                    await self.mock_p6_routing(packet)
                except Exception as e:
                    print(f"   ⚠️ Invalid packet: {e}")

            elif msg["type"] == "PEER_LIST":
                print(f"   👥 Online: {msg['online']}")

            elif msg["type"] == "ERROR":
                print(f"   ⚠️ Relay error: {msg.get('reason')}")

    async def send_via_relay(self, to_pubkey: str, packet: ProtocolPacket) -> bool:
        """透過 relay server 轉發封包"""
        if not self.srv_writer:
            print("⚠️  Not connected to relay")
            return False
        msg = json.dumps({
            "type": "FORWARD",
            "to_pubkey": to_pubkey,
            "packet": packet.model_dump()
        }) + "\n"
        self.srv_writer.write(msg.encode())
        await self.srv_writer.drain()
        print(f"📤 [Relay] Sent to {to_pubkey}")
        return True

    # ==========================================
    # 4. 模擬路由層 (Mock Routing)
    # ==========================================
    async def mock_p6_routing(self, packet: ProtocolPacket):
        """模擬 P6 收到封包後的反應"""
        if packet.type == "QUERY":
            print("   🧠 [Mock AI] 正在解析 Query... 假裝思考了 2 秒")
            await asyncio.sleep(2)
            print(f"   🧠 [Mock AI] 回應 Payload: {packet.payload}")

# ==========================================
# 4. 工具：取得本機 LAN IP
# ==========================================
def get_lan_ip() -> str:
    """取得本機在 LAN 上實際使用的 IP（不會真的連線，只用來查路由介面）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

# ==========================================
# 5. 主程式
# ==========================================
def build_test_packet(pubkey: str, peer_pubkey: str, message: str) -> ProtocolPacket:
    return ProtocolPacket(
        msg_id=str(uuid.uuid4()),
        type="QUERY",
        header=PacketHeader(
            sender_pubkey=pubkey,
            target_pubkey=peer_pubkey,
            timestamp=int(time.time()),
        ),
        payload={"query_text": message},
        signature="mock_signature"
    )

async def main(args):
    node = P2PNode(host="0.0.0.0", port=args.port, my_pubkey=args.pubkey)

    # ── Relay 模式（封包經 server 轉發，跨不同 WiFi）─────────
    if args.server_ip:
        print(f"🌐 [Mode] Relay 模式（Server: {args.server_ip}:{args.server_port}）")

        relay_task = asyncio.create_task(
            node.run_relay(args.server_ip, args.server_port)
        )
        await asyncio.sleep(1)  # 等 REGISTER 完成

        if args.peer_pubkey and args.peer_pubkey != "0xUNKNOWN":
            await asyncio.sleep(1)
            packet = build_test_packet(args.pubkey, args.peer_pubkey, args.message)
            await node.send_via_relay(args.peer_pubkey, packet)

        await relay_task  # 持續監聽

    # ── 直連模式（同一個 LAN）────────────────────────────────
    else:
        lan_ip = get_lan_ip()
        print(f"🏠 [Mode] 直連模式 | IP: {lan_ip}:{args.port} | pubkey: {args.pubkey}")

        asyncio.create_task(node.start_server())
        await asyncio.sleep(1)

        if args.peer_ip:
            if args.peer_port is None:
                print("⚠️  直連模式需要同時提供 --peer-ip 與 --peer-port")
                return
            await asyncio.sleep(2)
            packet = build_test_packet(args.pubkey, args.peer_pubkey, args.message)
            await node.send_packet(args.peer_ip, args.peer_port, packet)

        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedOut P2P Node")
    parser.add_argument("--port",        type=int, required=True,       help="本機監聽 port（例：8001）")
    parser.add_argument("--pubkey",      type=str, required=True,       help="本機 pubkey（例：0xNODE_A）")

    # Relay 模式（跨不同 WiFi）
    parser.add_argument("--server-ip",   type=str, default=None,        help="[Relay] Server IP")
    parser.add_argument("--server-port", type=int, default=9000,        help="[Relay] Server port（預設 9000）")
    parser.add_argument("--peer-pubkey", type=str, default="0xUNKNOWN", help="對方的 pubkey")
    parser.add_argument("--message",     type=str, default="Hello!",    help="測試封包內容")

    # 直連模式（同一個 LAN）
    parser.add_argument("--peer-ip",     type=str, default=None,        help="[直連] 對方 IP")
    parser.add_argument("--peer-port",   type=int, default=None,        help="[直連] 對方 port")

    args = parser.parse_args()

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n👋 [Exit] Node stopped.")