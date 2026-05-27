# LinkedOut — E2EE M2M Overlay

本地 AI agent 之間的端對端加密通訊層。封包經 relay server 轉發，但 **relay 只看得到密文與路由位址，無法解讀內容**。

## E2EE 設計
- **身分 = X25519 公鑰**：每個節點第一次啟動會在 `linkedout_<port>.key` 產生長期金鑰，其 public key (hex) 就是對外身分。把它分享給朋友、加入對方的 agent list（= proposal 的「與朋友交換 Public Key」）。
- **每則訊息加密**（Noise「X」單向模式）：寄件者產生一把**臨時金鑰 (ephemeral)** → forward secrecy；同時綁入自己的**靜態金鑰** → 寄件者身分驗證。兩段 Diffie-Hellman 經 HKDF-SHA256 導出對稱金鑰，再以 **ChaCha20-Poly1305 (AEAD)** 加密。
- **Metadata 綁定**：sender/target/msg_id/type 綁進 AEAD 的 AAD，relay 竄改 header 會導致解密失敗。
- **信任白名單 (`--trust`)**：不在白名單的寄件者直接拒收（防未授權存取 / DoS）。`--peer-pubkey` 指定的對象會自動加入信任。

驗證加密層：`python3 e2ee.py`（round-trip / 竄改偵測 / 寄件者偽造偵測 / 金鑰持久化）。

---

## 跑法（Relay 模式，可跨 WiFi）

### 0. Server
```
python3 relay_server.py --port 9000
```

### 1. 先各自啟動一次拿到自己的公鑰並交換
每個節點啟動時會印出：
```
🔑 My public key（分享給朋友，加入對方 agent list）:
   67d9f3ee54452a5e5c891a3b79ae4ffb703cd4fbcd874fb1723732d0dd6a0573
```
把 A、B 的公鑰互相交換（可設成環境變數 `A_PUB`、`B_PUB` 方便填）。

### 2. Peer A（收件者，信任 B）
```
python3 p2p_node.py --port 8001 --name A \
  --server-ip 140.112.30.188 --server-port 9000 \
  --trust $B_PUB
```

### 3. Peer B（送一則加密 QUERY 給 A）
```
python3 p2p_node.py --port 8002 --name B \
  --server-ip 140.112.30.188 --server-port 9000 \
  --peer-pubkey $A_PUB --message 'Hi from B'
```

預期：
- **Relay log**：`📦 ... → ...  | 🔒 payload encrypted (relay 看不懂內容)`
- **A 收到**：`🔓 [Decrypted] ... ✔ 寄件者已驗證` → Mock AI 回應
- 未授權的寄件者會看到：`⛔ [Reject] 未授權的寄件者 ...`

---

## 直連模式（同一 LAN）
```
# A（收件者）
python3 p2p_node.py --port 8001 --trust $B_PUB
# B（送給 A，需指定對方 IP / port / 公鑰）
python3 p2p_node.py --port 8002 --peer-ip 192.168.x.x --peer-port 8001 \
  --peer-pubkey $A_PUB --message 'Hi from B'
```

> ⚠️ `*.key` 是私鑰，已被 `.gitignore` 排除，請勿上傳。

---

## TODO
* 串接 API / JSON 格式
* ~~傳輸安全 (E2EE / Public Key)~~ ✅ 已完成（X25519 + ChaCha20-Poly1305 + 信任白名單）
* QUERY → RESPONSE 完整 round-trip（目前只單向，收到後是 Mock AI）
* 串接真實本地 AI 記憶檢索（取代 Mock AI）
* 檔案權限 / 三層知識庫（Personal / Task / Common Vault，暫時人工分類）
* mTLS（proposal 另一條安全路線，目前以應用層 E2EE 取代）
* 改 P2P（NAT 穿透，減少對 relay 依賴）
