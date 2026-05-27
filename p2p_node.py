import asyncio
import argparse
import json
import socket
import uuid
import time
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Set

import e2ee

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
    payload: Dict          # E2EE 模式下這裡裝的是密文 envelope，relay 看不懂
    signature: str

# ==========================================
# 2. 網路層實作 (Network Layer) + E2EE
# ==========================================
class P2PNode:
    def __init__(self, port: int, priv: "e2ee.X25519PrivateKey",
                 trust: Optional[Set[str]] = None, host: str = "0.0.0.0"):
        self.host = host
        self.port = port
        self.priv = priv
        self.my_pubkey = e2ee.public_hex(priv)   # 身分 = 真實 X25519 公鑰
        self.trust: Set[str] = trust or set()    # 信任白名單（允許的寄件者公鑰）
        self.server = None
        self.srv_reader: Optional[asyncio.StreamReader] = None
        self.srv_writer: Optional[asyncio.StreamWriter] = None

    # ── E2EE 輔助 ─────────────────────────────────────────
    @staticmethod
    def _aad(sender: str, target: Optional[str], msg_id: str, ptype: str) -> bytes:
        """把路由 metadata 綁進 AEAD，防止 relay 竄改 header。"""
        return f"{sender}|{target}|{msg_id}|{ptype}".encode()

    def build_packet(self, target_pubkey: str, ptype: str, app_payload: Dict) -> ProtocolPacket:
        """建立封包：app_payload 會以收件者公鑰加密後放進 payload。"""
        msg_id = str(uuid.uuid4())
        aad = self._aad(self.my_pubkey, target_pubkey, msg_id, ptype)
        envelope = e2ee.encrypt(self.priv, target_pubkey,
                                json.dumps(app_payload).encode(), aad)
        return ProtocolPacket(
            msg_id=msg_id,
            type=ptype,
            header=PacketHeader(
                sender_pubkey=self.my_pubkey,
                target_pubkey=target_pubkey,
                timestamp=int(time.time()),
            ),
            payload=envelope,
            signature="aead-x25519",   # 真實性由 AEAD + static DH 保證
        )

    async def handle_incoming(self, packet: ProtocolPacket):
        """收到封包：信任白名單檢查 → 解密 → 交給路由層。"""
        sender = packet.header.sender_pubkey

        # 信任白名單：未授權的寄件者直接拒收（proposal 的防 DoS / 未授權存取）
        if self.trust and sender not in self.trust:
            print(f"   ⛔ [Reject] 未授權的寄件者 {sender[:16]}…（不在信任白名單）")
            return

        env = packet.payload
        if not env.get("enc"):
            print("   ⚠️  [Plaintext] 封包未加密，略過（E2EE 模式只接受密文）")
            return

        aad = self._aad(sender, packet.header.target_pubkey, packet.msg_id, packet.type)
        try:
            plaintext = e2ee.decrypt(self.priv, sender, env, aad)
            app_payload = json.loads(plaintext.decode())
        except Exception as e:
            print(f"   🚫 [Decrypt failed] 簽章/金鑰不符或封包被竄改：{e}")
            return

        print(f"   🔓 [Decrypted] from {sender[:16]}… ✔ 寄件者已驗證")
        await self.mock_p6_routing(packet, app_payload)

    # ── Socket Server（直連模式用）────────────────────────
    async def start_server(self):
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
        print(f"🟢 [Server] Listening on {self.host}:{self.port}")
        async with self.server:
            await self.server.serve_forever()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        print(f"📥 [Server] Accepted connection from {addr}")
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                json_str = data.decode('utf-8').strip()
                try:
                    packet = ProtocolPacket.model_validate_json(json_str)
                    print(f"   ➔ [Received] Type: {packet.type} | MsgID: {packet.msg_id[:8]}… | From: {packet.header.sender_pubkey[:16]}…")
                    await self.handle_incoming(packet)
                except Exception as e:
                    print(f"   ⚠️ [Error] Invalid Packet format: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            print(f"🔴 [Server] Connection closed from {addr}")
            writer.close()
            await writer.wait_closed()

    async def send_packet(self, peer_ip: str, peer_port: int, packet: ProtocolPacket, retries=3) -> bool:
        """直連模式：主動發送封包（帶簡單 Retry）。"""
        for attempt in range(retries):
            try:
                _, writer = await asyncio.open_connection(peer_ip, peer_port)
                writer.write((packet.model_dump_json() + "\n").encode('utf-8'))
                await writer.drain()
                print(f"📤 [Sent] Type: {packet.type} to {peer_ip}:{peer_port}（🔒 已加密）")
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
        print(f"🔗 [Relay] Connecting to {server_ip}:{server_port} ...")
        self.srv_reader, self.srv_writer = await asyncio.open_connection(server_ip, server_port)

        reg = json.dumps({"type": "REGISTER", "pubkey": self.my_pubkey}) + "\n"
        self.srv_writer.write(reg.encode())
        await self.srv_writer.drain()

        ack = json.loads((await self.srv_reader.readline()).decode().strip())
        if ack.get("type") == "ACK":
            print(f"✅ [Relay] Registered as {self.my_pubkey[:16]}…")

        while True:
            data = await self.srv_reader.readline()
            if not data:
                print("⚠️  [Relay] Connection lost.")
                break
            msg = json.loads(data.decode().strip())

            if msg["type"] == "DELIVER":
                try:
                    packet = ProtocolPacket.model_validate(msg["packet"])
                    print(f"📥 [Relay] From: {packet.header.sender_pubkey[:16]}… | Type: {packet.type}")
                    await self.handle_incoming(packet)
                except Exception as e:
                    print(f"   ⚠️ Invalid packet: {e}")

            elif msg["type"] == "PEER_LIST":
                shown = [p[:16] + "…" for p in msg["online"]]
                print(f"   👥 Online: {shown}")

            elif msg["type"] == "ERROR":
                print(f"   ⚠️ Relay error: {msg.get('reason')}")

    async def send_via_relay(self, to_pubkey: str, packet: ProtocolPacket) -> bool:
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
        print(f"📤 [Relay] Sent to {to_pubkey[:16]}…（🔒 已加密，relay 無法解讀）")
        return True

    # ==========================================
    # 4. 模擬路由層 (Mock Routing)
    # ==========================================
    async def mock_p6_routing(self, packet: ProtocolPacket, app_payload: Dict):
        """模擬 P6 收到（已解密的）封包後的反應。"""
        if packet.type == "QUERY":
            print("   🧠 [Mock AI] 正在解析 Query... 假裝思考了 2 秒")
            await asyncio.sleep(2)
            print(f"   🧠 [Mock AI] 回應 Payload: {app_payload}")

# ==========================================
# 5. 工具：取得本機 LAN IP
# ==========================================
def get_lan_ip() -> str:
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
# 6. 主程式
# ==========================================
async def main(args):
    priv = e2ee.load_or_create_identity(args.key_file)

    trust: Set[str] = set()
    if args.trust:
        trust.update(t.strip() for t in args.trust.split(",") if t.strip())
    if args.peer_pubkey and args.peer_pubkey != "0xUNKNOWN":
        trust.add(args.peer_pubkey)   # 要對話的 peer 自動視為信任

    node = P2PNode(port=args.port, priv=priv, trust=trust)

    label = args.name or node.my_pubkey[:16]
    print("=" * 60)
    print(f"🦉 [LinkedOut] Node '{label}'")
    print(f"🔑 My public key（分享給朋友，加入對方 agent list）:\n   {node.my_pubkey}")
    if trust:
        print(f"🤝 信任白名單: {[t[:16] + '…' for t in trust]}")
    else:
        print("⚠️  未設定信任白名單（--trust）→ 將拒收所有人；請至少加入要對話的 peer")
    print("=" * 60)

    # ── Relay 模式（封包經 server 轉發，跨不同 WiFi）─────────
    if args.server_ip:
        print(f"🌐 [Mode] Relay 模式（Server: {args.server_ip}:{args.server_port}）")
        relay_task = asyncio.create_task(node.run_relay(args.server_ip, args.server_port))
        await asyncio.sleep(1)

        if args.peer_pubkey and args.peer_pubkey != "0xUNKNOWN":
            await asyncio.sleep(1)
            packet = node.build_packet(args.peer_pubkey, "QUERY", {"query_text": args.message})
            await node.send_via_relay(args.peer_pubkey, packet)

        await relay_task

    # ── 直連模式（同一個 LAN）────────────────────────────────
    else:
        lan_ip = get_lan_ip()
        print(f"🏠 [Mode] 直連模式 | IP: {lan_ip}:{args.port}")
        asyncio.create_task(node.start_server())
        await asyncio.sleep(1)

        if args.peer_ip:
            if args.peer_port is None:
                print("⚠️  直連模式需要同時提供 --peer-ip 與 --peer-port")
                return
            if not args.peer_pubkey or args.peer_pubkey == "0xUNKNOWN":
                print("⚠️  E2EE 需要 --peer-pubkey（對方的公鑰）才能加密")
                return
            await asyncio.sleep(2)
            packet = node.build_packet(args.peer_pubkey, "QUERY", {"query_text": args.message})
            await node.send_packet(args.peer_ip, args.peer_port, packet)

        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedOut P2P Node (E2EE)")
    parser.add_argument("--port",        type=int, required=True,       help="本機監聽 port（例：8001）")
    parser.add_argument("--key-file",    type=str, default=None,        help="X25519 金鑰檔（預設 linkedout_<port>.key）")
    parser.add_argument("--name",        type=str, default=None,        help="顯示用暱稱（純美觀，預設取公鑰前綴）")
    parser.add_argument("--trust",       type=str, default=None,        help="信任的寄件者公鑰，逗號分隔（白名單）")

    # Relay 模式（跨不同 WiFi）
    parser.add_argument("--server-ip",   type=str, default=None,        help="[Relay] Server IP")
    parser.add_argument("--server-port", type=int, default=9000,        help="[Relay] Server port（預設 9000）")
    parser.add_argument("--peer-pubkey", type=str, default="0xUNKNOWN", help="對方的公鑰（加密目標）")
    parser.add_argument("--message",     type=str, default="Hello!",    help="測試封包內容")

    # 直連模式（同一個 LAN）
    parser.add_argument("--peer-ip",     type=str, default=None,        help="[直連] 對方 IP")
    parser.add_argument("--peer-port",   type=int, default=None,        help="[直連] 對方 port")

    args = parser.parse_args()
    if args.key_file is None:
        args.key_file = f"linkedout_{args.port}.key"

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n👋 [Exit] Node stopped.")
