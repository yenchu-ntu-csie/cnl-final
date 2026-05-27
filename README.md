# LinkedOut — E2EE M2M Overlay

本地 AI agent 之間的端對端加密通訊層：節點以 **X25519 公鑰** 為身分，透過 relay server 轉發封包，
但 **relay 只看得到密文與路由位址，無法解讀內容**。應用層提供對對方分享資料夾 `share/` 的
**read / append / list** 操作，並以資料夾分區做權限控制。

## 模組架構（分層）
| 檔案 | 層 | 負責 |
|---|---|---|
| `e2ee.py` | 加密層 | X25519 金鑰、Noise-X 加解密 |
| `relay_server.py` | 中繼 | 依公鑰註冊/轉發封包（只看密文） |
| `p2p_node.py` | 網路層 | 封包、加解密、relay/直連、信任白名單、CLI |
| `app_layer.py` | 應用層 | 解析 JSON、`share/` 的 read/append/list + 權限/路徑檢查 |
| `agents.py` | 白名單 | 管理 `agents.json`（agent list / 信任名單） |
| `script/run_A.sh`, `run_B.sh` | 便利腳本 | 一鍵跑收訊方 / 送訊方 |

各模組可獨立自我測試：`python3 e2ee.py`、`python3 app_layer.py`。

---

## E2EE 設計
- **身分 = X25519 公鑰**：首次啟動在 `linkedout_<port>.key` 產生長期金鑰，其 public key (hex) 即對外身分。把它分享給朋友、加入對方 agent list（= proposal 的「與朋友交換 Public Key」）。
- **每則訊息加密**（Noise「X」單向模式）：寄件者產生**臨時金鑰 (ephemeral)** → forward secrecy；同時綁入自己的**靜態金鑰** → 寄件者身分驗證。兩段 Diffie-Hellman 經 HKDF-SHA256 導出對稱金鑰，再以 **ChaCha20-Poly1305 (AEAD)** 加密。
- **Metadata 綁定**：sender/target/msg_id/type 綁進 AEAD 的 AAD，relay 竄改 header 會導致解密失敗。

## 信任白名單（agent list）
- 持久化在 `agents.json`：節點啟動會載入，**接受清單內所有人**傳來的訊息，不在清單者直接拒收（防未授權存取 / DoS）。
- 管理：
  ```bash
  python3 agents.py add <對方公鑰> --name B   # 加入
  python3 agents.py list                       # 列出
  python3 agents.py remove <公鑰>             # 移除
  ```
- 臨時追加（不寫檔）：`--trust <pubkey,...>`；`--peer-pubkey` 指定的對象自動信任。

## 分享資料夾 share/ 與權限
雙方本地各有一個 `share/`，分兩區（節點啟動會自動建立）：

| 資料夾 | read | append |
|---|---|---|
| `share/read-only/` | ✅ | ❌ `permission_denied` |
| `share/read&append/` | ✅ | ✅ |

`app_layer.py` 在實際動檔案前先檢查：
1. **路徑安全**：解析後必須仍在 `share/` 內（擋 `../`、絕對路徑逃逸）→ 否則 `path_denied`
2. read/append 必須落在某個 zone → 否則 `not_shared`
3. append 只允許在 `read&append/` → 否則 `permission_denied`

## 應用層協定（解密後的明文 JSON）
**Request**（type=`REQUEST`）
```jsonc
{ "id": "8da83bdf", "op": "read|append|list", "path": "read-only/notes.md", "content": "append 才需要" }
```
**Response**（type=`RESPONSE`）
```jsonc
{ "id": "8da83bdf", "ok": true, "content": "read 回傳全文", "entries": ["read-only/", ...], "error": null }
```
- `op`：`read`（讀檔）/ `append`（追加，支援 `\n` 換行）/ `list`（看資料夾結構，path 可省略表示整個 share）
- `id`：請求/回應對應
- 錯誤碼：`path_denied` / `not_shared` / `permission_denied` / `not_found` / `bad_op` / `is_a_directory` / `io_error`

---

## 跑法 A：單機快速驗證（3 個終端機）
```bash
A_PUB=$(python3 -c "import e2ee;print(e2ee.public_hex(e2ee.load_or_create_identity('linkedout_8001.key')))")
B_PUB=$(python3 -c "import e2ee;print(e2ee.public_hex(e2ee.load_or_create_identity('linkedout_8002.key')))")
python3 agents.py add "$A_PUB" --name A; python3 agents.py add "$B_PUB" --name B
mkdir -p "share/read-only" "share/read&append"; echo "唯讀內容" > "share/read-only/notes.md"

# 終端1：relay
python3 relay_server.py --port 9000
# 終端2：A（收訊方，提供 share/）
python3 p2p_node.py --port 8001 --key-file linkedout_8001.key --name A --server-ip 127.0.0.1
# 終端3：B（對 A 操作）
python3 p2p_node.py --port 8002 --key-file linkedout_8002.key --name B --server-ip 127.0.0.1 \
  --peer-pubkey "$A_PUB" --op read   --path "read-only/notes.md"
python3 p2p_node.py --port 8002 --key-file linkedout_8002.key --name B --server-ip 127.0.0.1 \
  --peer-pubkey "$A_PUB" --op append --path "read&append/log.md" --content "第一行\n第二行\n"
python3 p2p_node.py --port 8002 --key-file linkedout_8002.key --name B --server-ip 127.0.0.1 \
  --peer-pubkey "$A_PUB" --op list
```

## 跑法 B：兩台機器（用腳本，可跨 WiFi）
腳本預設 relay 在 `140.112.30.183:9000`，不同就 `RELAY_IP=實際IP ./script/...`。

```bash
# relay 機器
python3 relay_server.py --port 9000

# 兩台各跑一次拿公鑰並交換
./script/run_A.sh      # 印 A 公鑰
./script/run_B.sh      # 印 B 公鑰

# 電腦 A（收訊方）：把 B 加白名單 + 建 share/ + 開始聽
./script/run_A.sh <B的公鑰> B

# 電腦 B（送訊方）
./script/run_B.sh <A的公鑰> list                                  # 看 A 的 share 結構
./script/run_B.sh <A的公鑰> read   "read-only/notes.md"           # 讀
./script/run_B.sh <A的公鑰> append "read&append/log.md" "一行\n"   # 追加
```

預期：relay 顯示 `🔒 payload encrypted`；A 顯示 `🔓 Decrypted ✔` + `📂 Request`；B 顯示 `✅ Reply`（read 印內容、list 印樹狀、append 印成功）；被擋的會回 `permission_denied` / `path_denied`。

> ⚠️ `read&append` 含 `&`，在 shell **路徑要用引號**包住（`"read&append/log.md"`），否則 `&` 會被當背景執行。
> ⚠️ `--content` 的 `\n` `\t` 會被轉成真正換行/Tab。
> ⚠️ 公鑰要用節點啟動印出的、或 relay `👥 Online` 顯示的那把，別用文件範例值，否則 relay 回 `peer_offline`。

## 直連模式（同一 LAN，不經 relay）
```bash
# A（收訊方）
python3 p2p_node.py --port 8001 --name A
# B（送給 A，需對方 IP / port / 公鑰）
python3 p2p_node.py --port 8002 --name B --peer-ip 192.168.x.x --peer-port 8001 \
  --peer-pubkey "$A_PUB" --op read --path "read-only/notes.md"
```
> 直連模式目前為單向（A 會執行操作，但 RESPONSE 尚未回傳）。

---

## 私密檔案（已被 `.gitignore` 排除，請勿上傳）
`*.key`（私鑰）、`agents.json`（個人白名單）、`share/`（個人分享內容）。

## NAT / P2P 現況
跨網際網路目前走 **relay 中繼**。曾以 UDP 打洞測試直連，但校園網與行動網路（CGNAT）皆為 **symmetric NAT**，
無法穿透 → 維持 relay（等同 WebRTC ICE 在無法打洞時退回 TURN 的行為）。同一 LAN 可用直連模式達成真 P2P。

## TODO
* ~~傳輸安全 E2EE / Public Key~~ ✅（X25519 + ChaCha20-Poly1305 + agent list）
* ~~REQUEST/RESPONSE round-trip~~ ✅（read / append / list，含 id 對應）
* ~~檔案分區權限 / 路徑安全~~ ✅（read-only / read&append + share/ 沙箱）
* per-peer 權限（哪個 peer 能存取哪些 zone/檔，而非全體一致）
* 串接真實本地 AI / 語意檢索（query 操作，對應 proposal 的跨節點聯合檢索）
* 三層知識庫對應（Personal / Task / Common Vault）
* 直連模式雙向回覆
* 穩定性：RESPONSE timeout、relay 斷線重連、封包遺失測試
