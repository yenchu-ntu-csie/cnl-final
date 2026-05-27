import asyncio
import argparse
import json
import re
import socket
import uuid
import time
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Set

import e2ee
import agents
import app_layer

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

# 應用層 payload（FileRequest / FileResponse）的 schema 與處理在 app_layer.py

# ==========================================
# 2. 網路層實作 (Network Layer) + E2EE
# ==========================================
class P2PNode:
    def __init__(self, port: int, priv: "e2ee.X25519PrivateKey",
                 trust: Optional[Set[str]] = None, host: str = "0.0.0.0",
                 share: str = "share", owner: str = "Anonymous",
                 agent_meta: Optional[Dict[str, dict]] = None,
                 model: Optional[str] = None):
        self.host = host
        self.port = port
        self.priv = priv
        self.my_pubkey = e2ee.public_hex(priv)   # 身分 = 真實 X25519 公鑰
        self.trust: Set[str] = trust or set()    # 信任白名單（允許的寄件者公鑰）
        self.share = share                       # 分享資料夾（read-only / read&append，權限在 app_layer 檢查）
        self.owner = owner                       # 給本機 AI 介紹自己身分用（ask op）
        self.agent_meta = agent_meta or {}       # pubkey → {name, tier?, ...}，未來放 ACL 用
        self.model = model                       # 給 ask op 用的 Ollama 模型；None 走 env/auto
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

        if packet.type == "REQUEST":
            # 應用層處理 → 拿回要回傳的 RESPONSE payload → 加密送回原寄件者
            tier = (self.agent_meta.get(sender) or {}).get("tier", "Common")
            resp_payload = await app_layer.handle_request(
                app_payload, self.share,
                owner=self.owner, sender_pubkey=sender, tier=tier,
                model=self.model,
            )
            if resp_payload is not None:
                pkt = self.build_packet(sender, "RESPONSE", resp_payload)
                if self.srv_writer:
                    await self.send_via_relay(sender, pkt)
                else:
                    print("   ⚠️ 直連模式尚未接回應通道（結果已在上面顯示）")
        elif packet.type == "RESPONSE":
            app_layer.handle_response(app_payload)
        else:
            print(f"   ⚠️ 未知封包類型: {packet.type}")

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
# 4. 工具：取得本機 LAN IP
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
# 5. 主程式
# ==========================================
_ESCAPES = {"\\n": "\n", "\\t": "\t", "\\r": "\r", "\\\\": "\\"}

def interpret_escapes(s: Optional[str]) -> Optional[str]:
    r"""把命令列輸入的 \n \t \r 轉成真正的換行/Tab（UTF-8 安全；\\ 可保留字面反斜線）。"""
    if not s:
        return s
    return re.sub(r"\\[ntr\\]", lambda m: _ESCAPES[m.group()], s)

def build_request_payload(args) -> Optional[Dict]:
    """把 CLI 參數組成 REQUEST payload（實際組裝在 app_layer.make_request）。"""
    if args.op in ("read", "append") and not args.path:
        print("⚠️  read/append 需要 --path（要操作哪個檔）")
        return None
    if args.op == "ask" and not args.query:
        print("⚠️  ask 需要 --query（要問對方 AI 什麼問題）")
        return None
    return app_layer.make_request(
        args.op,
        args.path or "",
        interpret_escapes(args.content),
        query=args.query,
    )


# ==========================================
#   REPL：互動式輸入（人類在這裡打字下指令給對方 AI）
# ==========================================
_REPL_HELP = (
    "指令：\n"
    "  <任意文字>            送 ask 到對方的 AI（預設模式）\n"
    "  /ask <text>           同上，顯式版本\n"
    "  /read <path>          讀對方 share/ 內的檔（如 read-only/notes.md）\n"
    "  /append <path> <text> 追加到對方 share/read&append/ 內的檔\n"
    "  /list [path]          列出對方 share/ 的結構\n"
    "  /help                 顯示這個說明\n"
    "  /quit, /exit, Ctrl-D  離開\n"
)


def _parse_repl_line(line: str) -> Optional[Dict]:
    """把使用者輸入的一行轉成 REQUEST payload；不合法回 None。"""
    line = line.strip()
    if not line:
        return None
    if line.startswith("/ask "):
        return app_layer.make_request("ask", query=line[5:].strip())
    if line.startswith("/read "):
        return app_layer.make_request("read", path=line[6:].strip())
    if line.startswith("/append "):
        rest = line[len("/append "):].strip()
        path, _, content = rest.partition(" ")
        if not path or not content:
            print("⚠️  /append 需要 <path> <text>")
            return None
        return app_layer.make_request("append", path=path,
                                       content=interpret_escapes(content))
    if line == "/list" or line.startswith("/list "):
        path = line[len("/list"):].strip()
        return app_layer.make_request("list", path=path)
    if line.startswith("/"):
        print(f"⚠️  未知指令：{line.split()[0]}（試試 /help）")
        return None
    # 沒有斜線開頭 → 預設為 ask
    return app_layer.make_request("ask", query=line)


async def repl_loop(node: "P2PNode", peer_pubkey: str, peer_label: str):
    """讓使用者持續輸入問題 / 指令送給對方；回應由背景 run_relay → handle_incoming 印出。"""
    print()
    print(f"💬 [REPL] 已連到 relay，現在和 {peer_label}({peer_pubkey[:8]}…) 對話。")
    print(_REPL_HELP)
    while True:
        try:
            line = await asyncio.to_thread(input, "linkedout> ")
        except (EOFError, KeyboardInterrupt):
            print("\n👋 bye")
            return
        if line.strip() in ("/quit", "/exit"):
            print("👋 bye")
            return
        if line.strip() == "/help":
            print(_REPL_HELP)
            continue
        req = _parse_repl_line(line)
        if req is None:
            continue
        packet = node.build_packet(peer_pubkey, "REQUEST", req)
        detail = req.get("path") or req.get("query", "")
        print(f"📤 送出 REQUEST id={req['id']} op={req['op']} {detail}")
        await node.send_via_relay(peer_pubkey, packet)
        # 等一下再印下一個 prompt，讓回應有機會先顯示出來（不阻塞，只是體感）
        await asyncio.sleep(0.05)


async def main(args):
    priv = e2ee.load_or_create_identity(args.key_file)
    app_layer.ensure_share(args.share)   # 確保 share/read-only 與 share/read&append 存在

    # 白名單來源：agents.json（持久化 agent list）為主，--trust / --peer-pubkey 為臨時追加
    agent_list = agents.load(args.agents_file)
    trust: Set[str] = set(agent_list.keys())
    if args.trust:
        trust.update(t.strip() for t in args.trust.split(",") if t.strip())
    if args.peer_pubkey and args.peer_pubkey != "0xUNKNOWN":
        trust.add(args.peer_pubkey)   # 要對話的 peer 自動視為信任

    node = P2PNode(port=args.port, priv=priv, trust=trust, share=args.share,
                   owner=args.name or "Anonymous", agent_meta=agent_list,
                   model=args.model)

    label = args.name or node.my_pubkey[:16]
    print("=" * 60)
    print(f"🦉 [LinkedOut] Node '{label}'")
    print(f"🔑 My public key（分享給朋友，加入對方 agent list）:\n   {node.my_pubkey}")
    if trust:
        names = [f"{(agent_list.get(pk) or {}).get('name') or pk[:8]}({pk[:8]}…)" for pk in trust]
        print(f"🤝 信任白名單（{len(trust)} 人，來自 {args.agents_file}）: {names}")
    else:
        print(f"⚠️  白名單是空的（{args.agents_file} 沒有任何人）→ 將拒收所有訊息")
        print("   用 `python3 agents.py add <對方公鑰> --name X` 加入後再啟動")
    print("=" * 60)

    # ── Relay 模式（封包經 server 轉發，跨不同 WiFi）─────────
    if args.server_ip:
        print(f"🌐 [Mode] Relay 模式（Server: {args.server_ip}:{args.server_port}）")
        relay_task = asyncio.create_task(node.run_relay(args.server_ip, args.server_port))
        await asyncio.sleep(1)

        peer_set = args.peer_pubkey and args.peer_pubkey != "0xUNKNOWN"

        if args.repl:
            if not peer_set:
                print("⚠️  REPL 模式需要 --peer-pubkey（要跟誰對話）")
            else:
                await asyncio.sleep(0.5)
                peer_label = (agent_list.get(args.peer_pubkey) or {}).get("name") or "peer"
                repl_task = asyncio.create_task(repl_loop(node, args.peer_pubkey, peer_label))
                done, pending = await asyncio.wait(
                    {relay_task, repl_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                return
        elif peer_set:
            await asyncio.sleep(1)
            req = build_request_payload(args)
            if req:
                packet = node.build_packet(args.peer_pubkey, "REQUEST", req)
                detail = req.get("path") or req.get("query", "")
                print(f"📤 送出 REQUEST id={req['id']} op={req['op']} {detail}")
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
            req = build_request_payload(args)
            if req:
                packet = node.build_packet(args.peer_pubkey, "REQUEST", req)
                detail = req.get("path") or req.get("query", "")
                print(f"📤 送出 REQUEST id={req['id']} op={req['op']} {detail}")
                await node.send_packet(args.peer_ip, args.peer_port, packet)

        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedOut P2P Node (E2EE)")
    parser.add_argument("--port",        type=int, required=True,       help="本機監聽 port（例：8001）")
    parser.add_argument("--key-file",    type=str, default=None,        help="X25519 金鑰檔（預設 linkedout_<port>.key）")
    parser.add_argument("--name",        type=str, default=None,        help="顯示用暱稱（純美觀，預設取公鑰前綴）")
    parser.add_argument("--agents-file", type=str, default="agents.json", help="agent list / 信任白名單檔（預設 agents.json）")
    parser.add_argument("--trust",       type=str, default=None,        help="額外臨時信任的寄件者公鑰，逗號分隔（不寫檔）")

    # Relay 模式（跨不同 WiFi）
    parser.add_argument("--server-ip",   type=str, default=None,        help="[Relay] Server IP")
    parser.add_argument("--server-port", type=int, default=9000,        help="[Relay] Server port（預設 9000）")
    parser.add_argument("--peer-pubkey", type=str, default="0xUNKNOWN", help="對方的公鑰（加密目標）")

    # 應用層：要對對方做的檔案操作
    parser.add_argument("--op",          type=str, default="read", choices=["read", "append", "list", "ask"], help="操作：read / append / list / ask")
    parser.add_argument("--path",        type=str, default=None,        help="要操作的檔案（相對對方 share/，含 zone，如 read-only/notes.md）；list / ask 可省略")
    parser.add_argument("--content",     type=str, default=None,        help="append 的內容（支援 \\n 換行、\\t Tab）")
    parser.add_argument("--query",       type=str, default=None,        help="ask 要問對方 AI 的自然語言問題")
    parser.add_argument("--repl",        action="store_true",           help="進入互動模式：在 prompt 持續輸入問題/指令（需 --peer-pubkey）")
    parser.add_argument("--share",       type=str, default="share",     help="本機分享資料夾（預設 share/）")
    parser.add_argument("--model",       type=str, default=None,        help="ask 用的 Ollama 模型；不指定時走 LINKEDOUT_MODEL / OLLAMA_MODEL 環境變數，再不然挑本機第一個已安裝的")

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
