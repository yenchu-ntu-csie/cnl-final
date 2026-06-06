# LinkedOut — E2EE M2M Overlay for Local AI Agents

本地 AI agent 之間的端對端加密通訊層：節點以 **X25519 公鑰** 為身分，
透過 relay server 轉發封包，但 **relay 只看得到密文與路由位址，無法解讀內容**。
應用層提供 **read / append / list / ask / capability** 五種操作；`ask` 讓對方的本地 Ollama
依 `share/` 內容回答你的問題，支援 **autonomous 模式**（單輪、多輪 follow-up、或**三人以上群組討論** ——
主持人對多 peer 平行 `capability` 能力探測 + 針對性追問 + 合成）。

---

## 1. 模組架構（由低到高分層）

| 檔案 | 層 | 負責 |
|---|---|---|
| `e2ee.py` | 加密 | X25519、Noise-X handshake、ChaCha20-Poly1305 AEAD、AAD 綁定 |
| `relay_server.py` | 中繼 | 依公鑰註冊 / 轉發封包（只看 header，看不到 payload） |
| `p2p_node.py` | 網路層 | 封包、加解密、relay / 直連、信任白名單、CLI、REPL、autonomous loop |
| `app_layer.py` | 應用層 | 解析 JSON、`share/` 四區的權限/路徑檢查、執行 read/append/list/ask |
| `ai_client.py` | AI | 本地 Ollama HTTP 包裝；含 `answer` / `synthesize` / `formulate` / `next_step` / `summarize` / `capability_probe` / `plan_question` / `group_summarize`（嚴格 prompt 分隔） |
| `agents.py` | 白名單 | 管理 `agents.json`（agent list + per-peer tier） |
| `share_audit.py` | 稽核工具 | 離線檢查某個 peer/tier 能 list/read/ask 到哪些 `share/` 路徑與 context 統計（不印內容） |
| `script/run_A.sh` `run_B.sh` | 便利腳本 | 一鍵跑收訊方 / 送訊方 |
| `setup.md` | 文件 | 環境準備、兩台機器部署 |

每層可獨立自我測試：`python3 e2ee.py`、`python3 app_layer.py`、`python3 ai_client.py "問題"`。

---

## 2. 安全與權限

### 2.1 E2EE 設計
- **身分 = X25519 公鑰**：首次啟動在 `linkedout_<port>.key` 產生長期金鑰，其 public key (hex) = 對外身分。
- **每則訊息加密**（Noise「X」單向模式）：寄件者產生**臨時金鑰 (ephemeral)** → forward secrecy；同時綁入**靜態金鑰** → 寄件者身分驗證。兩段 DH → HKDF-SHA256 → ChaCha20-Poly1305 (AEAD) 加密。
- **Metadata 綁定**：sender / target / msg_id / type 綁進 AEAD 的 AAD —— relay 改 header 就解不開。

### 2.2 信任白名單（agent list）
持久化在 `agents.json`，節點啟動會載入並接受**清單內所有人**傳來的訊息。不在清單者 fail-closed 直接拒收。

```bash
python3 agents.py add <對方公鑰> --name B --tier common   # common / task / personal
python3 agents.py set-tier <對方公鑰> task
python3 agents.py list
python3 agents.py remove <對方公鑰>
```

### 2.3 Per-peer tier ACL
每個 peer 在 `agents.json` 有一個 `tier`：`common < task < personal`。tier 不夠 → 一律回 `not_shared`（**不洩漏該檔/該區是否存在**）。

| zone | 需要 tier | read | append |
|---|---|---|---|
| `share/read-only/` | common | ✅ | ❌ `permission_denied` |
| `share/read&append/` | common | ✅ | ✅ |
| `share/task/` | task | ✅ | ❌ |
| `share/personal/` | personal | ✅ | ❌ |

`ask` 也受 tier 限制 —— A 端只把對方 tier 能看的 zone 內容餵給 Ollama，更高權限的 zone 連 LLM 視野都看不到。
`ask`/`capability` context 也會跳過 realpath 逃出 `share/` 的 symlink，避免把 zone 裡的捷徑變成越權讀取。

啟動節點或調高 tier 前，可以先跑離線稽核：

```bash
python3 share_audit.py --peer-pubkey <peer-pubkey>
python3 share_audit.py --tier task --share share --json
```

稽核只顯示路徑、zone 權限與 `ask` context 的 chunk/byte 統計，不列印檔案內容。

### 2.4 路徑安全
`_check` 解析後若不在 `share/` 內 → `path_denied`（擋 `../`、絕對路徑、symlink 逃逸）。

---

## 3. 應用層協定（解密後的明文 JSON）

### Request（packet `type="REQUEST"`）
```jsonc
{
  "id": "8da83bdf",
  "op": "read | append | list | ask | capability",
  "path": "read-only/notes.md",   // read/append 必填；list 可省；ask/capability 不用
  "content": "appended line\n",   // append 才需要
  "query": "你最喜歡哪本書?",       // ask 才需要
  "topic": "AI 安全",              // capability 才需要：要探測的題目
  "mode": "remote | local"        // ask 才用，預設 remote
}
```

### Response（packet `type="RESPONSE"`）
```jsonc
{
  "id": "8da83bdf",
  "ok": true,
  "content": "...",                                          // read 成功
  "entries": ["read-only/", "read-only/notes.md", ...],      // list 成功
  "answer":  "...",                                          // ask remote 成功
  "context": ["..."],                                        // ask local 成功（原始 chunks）
  "capability": {"relevant": true, "topics": [...], "summary": "..."},  // capability 成功
  "error":   "not_shared | permission_denied | path_denied | not_found | bad_op | is_a_directory | io_error | missing_query | missing_topic | ai_error"
}
```

### `ask` 兩個模式
| 模式 | A 端做什麼 | 離開 A 的內容 | 適用 |
|---|---|---|---|
| `remote`（預設） | A 的 AI 讀自己 share/ → 統整答案 | 只有「合成的文字」 | A 想保護原始資料 |
| `local` | A 只回 tier 過濾後的原始 chunks | 「過濾後的原文片段」 | B 想用自己模型 / 看原文 |

兩模式都通過同一條 tier ACL，不是安全機制、是策略選擇。

### `capability`（能力探測，用於群組討論）
A 的 AI 看自己 tier 內 share/，自評對某 topic 有沒有資料、哪些面向能說。
群組討論 (`--auto --peer-pubkey "p1,p2,..."`) 時，主持人會平行探測所有 peer，
再針對「有料的人」深問。Tier ACL 仍 enforce（peer 只能評估自己 tier 內看得到的 zone）。

---

## 4. 五種使用方式

| 方式 | 場景 | 範例 |
|---|---|---|
| **單一指令** | 一次性問答 | `--op ask --query "問題"` |
| **REPL 互動** | 連續多輪輸入 | `--repl`（預設純文字 = ask；`/read /append /list /local /remote /help`） |
| **Autonomous 單輪** | B 的 AI 自己想問題 | `--auto --goal "高層目標"` |
| **Autonomous 多輪** | B 看答案決定追問或收尾 | `--auto --rounds 5 --goal "..."` |
| **Autonomous 群組討論** | 主持人對多 peer 平行能力探測 + 針對性追問 | `--auto --peer-pubkey "p1,p2,p3" --rounds 4 --goal "..."` |
| **檔案操作** | read / append / list | `--op read --path read-only/notes.md` |
| **直連模式** | 同一 LAN，不經 relay | `--peer-ip <IP> --peer-port <PORT>`（雙向 RESPONSE 已支援） |

---

## 5. 本地測試 Guideline（單機，三個終端機）

### 5.1 前置檢查

```bash
cd ~/Documents/大三/CNL/cnl-final

# Python 套件
pip install -r requirements.txt   # 或 pip install pydantic cryptography
python3 -c "import e2ee, agents, app_layer, ai_client, p2p_node; print('imports OK')"

# Ollama daemon（用於 ask / autonomous）
curl -s --max-time 2 http://localhost:11434/api/tags | head -c 200
# 沒回的話：
#   cd ../ollama && OLLAMA_MODELS="$PWD/models" nohup ./ollama serve > /tmp/ollama.log 2>&1 & disown

# 模組自我測試（不碰網路）
python3 e2ee.py        # 加密層
python3 app_layer.py   # 應用層（read/append/list/ask schema + tier + 路徑安全）
```

### 5.2 一次性 setup（隔離資料夾，不污染現有 keys/agents.json）

```bash
mkdir -p _test && cd _test

# 兩把身分
A_PUB=$(python3 -c "import sys;sys.path.insert(0,'..');import e2ee;print(e2ee.public_hex(e2ee.load_or_create_identity('linkedout_A.key')))")
B_PUB=$(python3 -c "import sys;sys.path.insert(0,'..');import e2ee;print(e2ee.public_hex(e2ee.load_or_create_identity('linkedout_B.key')))")
echo "A=$A_PUB"; echo "B=$B_PUB"

# 互相加白名單（agents.json 寫在 _test/ 內，不影響專案根目錄）
python3 ../agents.py add "$A_PUB" --name Alice --tier personal
python3 ../agents.py add "$B_PUB" --name Bob   --tier common
python3 ../agents.py list

# Alice 的 share/ 四區各放一個有特色的檔
mkdir -p share/read-only share/read\&append share/task share/personal
printf 'Alice 的公開資訊：\n- 最喜歡的書：《雪崩》Snow Crash\n- 興趣：科幻、密碼學、登山\n' \
  > share/read-only/about_alice.md
printf '# 工作筆記（task tier 限定）\nAlice 在做 LinkedOut 計網期末專案，明天 demo。\n' \
  > share/task/work_notes.md
printf '# 私密日記（personal tier 限定）\nAlice 暗戀的人是...（祕密）\n' \
  > share/personal/diary.md
echo "(Bob 等等會 append 到這裡)" > share/read\&append/log.md

find share -type f | sort
```

### 5.3 開三個終端機

**終端 ① relay**
```bash
cd ~/Documents/大三/CNL/cnl-final/_test
python3 ../relay_server.py --port 19000
```

**終端 ② Alice = A 收訊方**（共用第三終端機要用的 _test 目錄）
```bash
cd ~/Documents/大三/CNL/cnl-final/_test
python3 ../p2p_node.py --port 18001 --key-file linkedout_A.key --name Alice \
  --server-ip 127.0.0.1 --server-port 19000
# 看到 ✅ [Relay] Registered as ... 就 OK
```

**終端 ③ Bob = B 送訊方**（每次測試獨立跑，Ctrl-C 結束再下一條）
```bash
cd ~/Documents/大三/CNL/cnl-final/_test
A_PUB=$(python3 -c "import sys;sys.path.insert(0,'..');import e2ee;print(e2ee.public_hex(e2ee.load_or_create_identity('linkedout_A.key')))")
NODE_B=(python3 ../p2p_node.py --port 18002 --key-file linkedout_B.key --name Bob
        --server-ip 127.0.0.1 --server-port 19000 --peer-pubkey "$A_PUB")
```

### 5.4 測試項目

| # | 指令 | 預期 |
|---|---|---|
| T1 | `"${NODE_B[@]}" --op list` | 只看到 `read-only/` + `read&append/`（common tier） |
| T2 | `"${NODE_B[@]}" --op read --path read-only/about_alice.md` | 印出 Alice 公開資訊 |
| T3 | `"${NODE_B[@]}" --op read --path personal/diary.md` | ❌ `not_shared`（tier 不夠，不洩漏存在） |
| T4 | `"${NODE_B[@]}" --op append --path "read&append/log.md" --content "Bob says hi\n"` | ✅，且 `cat share/read\&append/log.md` 有新行 |
| T5 | `"${NODE_B[@]}" --op append --path read-only/about_alice.md --content "改" ` | ❌ `permission_denied` |
| T6 | `"${NODE_B[@]}" --op ask --query "你最喜歡哪本書?" --mode remote` | A 的 Ollama 答《雪崩》 |
| T7 | `"${NODE_B[@]}" --op ask --query "三項朋友的興趣" --mode local` | B 收 chunks → 自己 Ollama 合成 |
| T8 | `"${NODE_B[@]}" --auto --goal "我想知道朋友最喜歡哪本書"` | **B 的 AI 自動生問題 → 送 A → 收答案**（autonomous 單輪） |
| T8b | `"${NODE_B[@]}" --auto --rounds 5 --goal "我想完整了解我這個朋友"` | **多輪 follow-up**：LLM 看答案決定追問或收尾 |
| T8c | **三人群組討論**（見 §5.5b） | **平行能力探測 + 主持人選人追問 + 多 peer 合成** |
| T9 | `"${NODE_B[@]}" --repl` | 進互動模式；打字 = ask，`/list`、`/read`、`/append`、`/local`、`/remote`、`/quit` |

預期 T8（單輪）輸出範例：
```
🎯 [Auto] 目標：我想知道朋友最喜歡哪本書（最多 1 輪）
── round 1/1 ──
🤖 [Auto/r1] 問：What book does your friend enjoy reading most?
📤 [Auto] 送出 REQUEST id=... op=ask
🤖 [Reply id=...] AI 回應：
┌─────────────
│ Alice 最喜歡的書是《雪崩》Snow Crash。
└─────────────
```

預期 T8b（多輪）輸出範例：
```
🎯 [Auto] 目標：我想完整了解我這個朋友（最多 5 輪）
── round 1/5 ──
🤖 [Auto/r1] 問：你最近在忙什麼？
🤖 [Reply id=...] AI 回應：│ 我在做計網期末專案 LinkedOut...
── round 2/5 ──
🤖 [Auto/r2] 問：你的興趣愛好有哪些？
🤖 [Reply id=...] AI 回應：│ 科幻小說《雪崩》《三體》、Rust、登山...
── round 3/5 ──
✅ [Auto] LLM 在 2 輪後決定收尾
📝 [Auto] 最終整理：
你的朋友 Alice 正在做 LinkedOut 期末專案，喜歡科幻、Rust 和登山...
```

多輪行為說明：
- round 1 一定會 ask（用 `formulate` 生成第一個問題）
- round 2 起 LLM 看歷史 Q/A 自己決定：再問（`{"action":"ask"}`）或收尾（`{"action":"done"}`）
- 跑滿 `--rounds N` 還沒 done → 呼叫 `summarize` 強制整理收尾
- LLM 若早早 done → 直接用它給的 summary 結束

### 5.5 tier 升級 demo（亮點）

升 Bob 成 `task`，**A 端要重啟才會重新載入** `agents.json`：
```bash
python3 ../agents.py set-tier "$B_PUB" task
# 終端 ② Ctrl-C，重新啟動 Alice
#   應該印 🤝 Bob [task]
```

接著 B 測試：
```bash
"${NODE_B[@]}" --op list
# → 多出 task/

"${NODE_B[@]}" --op read --path task/work_notes.md
# → 成功讀工作筆記

"${NODE_B[@]}" --op read --path personal/diary.md
# → 仍 not_shared

"${NODE_B[@]}" --op ask --query "Alice 最近在忙什麼?"
# → AI 答 LinkedOut 專案（因為 task/ 內容進了 LLM context）
```

升 `personal` 再測一次：`set-tier "$B_PUB" personal` → 重啟 A → `read personal/diary.md` 成功。

### 5.5b 三人群組討論 demo（亮點）

主持人 Carol 平行探測 Alice、Bob 對某題目的能力，再針對性追問、合成。

```bash
cd ~/Documents/大三/CNL/cnl-final
mkdir -p _group && cd _group

# 三把身分
A_PUB=$(python3 -c "import sys;sys.path.insert(0,'..');import e2ee;print(e2ee.public_hex(e2ee.load_or_create_identity('linkedout_A.key')))")
B_PUB=$(python3 -c "import sys;sys.path.insert(0,'..');import e2ee;print(e2ee.public_hex(e2ee.load_or_create_identity('linkedout_B.key')))")
C_PUB=$(python3 -c "import sys;sys.path.insert(0,'..');import e2ee;print(e2ee.public_hex(e2ee.load_or_create_identity('linkedout_C.key')))")
python3 ../agents.py add "$A_PUB" --name Alice --tier common
python3 ../agents.py add "$B_PUB" --name Bob   --tier common
python3 ../agents.py add "$C_PUB" --name Carol --tier personal

# 三個獨立 share/，各放對立觀點
for d in share_A share_B share_C; do mkdir -p "$d/read-only" "$d/read&append" "$d/task" "$d/personal"; done
printf '# Alice：強監管派\nAI 安全是當前最重要的議題。必須強制監管、設定紅線、要求透明度。\n' > share_A/read-only/views.md
printf '# Bob：自由發展派\nAI 安全議題被誇大了。AI 是工具，風險來自人類使用方式，不是技術本身。\n' > share_B/read-only/views.md
printf '# Carol：平衡派\n高風險領域強監管，低風險領域留空間給創新。國際協作很重要。\n' > share_C/read-only/views.md
```

開四個終端機：
```bash
# 終端 ① relay
python3 ../relay_server.py --port 9000

# 終端 ② Alice
python3 ../p2p_node.py --port 8001 --key-file linkedout_A.key --name Alice --share share_A \
  --server-ip 127.0.0.1 --server-port 9000

# 終端 ③ Bob
python3 ../p2p_node.py --port 8002 --key-file linkedout_B.key --name Bob --share share_B \
  --server-ip 127.0.0.1 --server-port 9000

# 終端 ④ Carol（主持人，--auto 對兩個 peer）
python3 ../p2p_node.py --port 8003 --key-file linkedout_C.key --name Carol --share share_C \
  --server-ip 127.0.0.1 --server-port 9000 \
  --peer-pubkey "$A_PUB,$B_PUB" --auto --rounds 4 --goal "AI 安全該不該強監管"
```

預期 Carol 輸出：
```
🎯 [Auto] 目標：AI 安全該不該強監管（最多 4 輪追問；對話對象 2 人）
👥 [Auto] Peers: ['Alice', 'Bob']
🧠 [Auto] 我自己 share/ 有 1 段資料可帶上桌

══ Phase 1：capability scan ══
📤 探測 Alice... 📤 探測 Bob...
🔍 Alice: ✅ 有相關  面向：[AI 安全, 強制監管]
🔍 Bob:   ❌ 無相關（小模型偶有保守判斷；plan_question 仍會視需要追問）

══ Phase 2：targeted query ══
── round 1/4 ── 🤖 → Alice: 在您看來，哪些方面需要強監管？
  Alice 回：算法透明度、資料隱私、自動化決策...
── round 2/4 ── 🤖 → Bob: 您認為AI安全需要強監管嗎？
  Bob 回：不需要，會扼殺創新...
── round 3/4 ── ✅ LLM 在 2 輪後決定收尾
📝 最終整理：Alice 提出...應該強監管...Bob 認為...會扼殺創新...
```

亮點：
- **Phase 1 平行探測**（用 `asyncio.gather`）—— 不會 N 個 peer 慢慢串行
- **Phase 2 智慧路由** —— LLM 看 capability map 選人選題目，不浪費 token 盲問
- **summary 明確點名** —— 「Alice 認為 X / Bob 認為 Y / 你認為 Z」對比清楚
- **協定 + 加密 + tier ACL 全程沿用** —— relay 看到的還是 🔒 payload encrypted

---

### 5.6 觀察點：relay log
任何時候看終端 ① relay：
```
📦 <sender>… → <recipient>…  | 🔒 payload encrypted (relay 看不懂內容)
```
**雙向各印一次**（REQUEST 一次、RESPONSE 一次），證明 relay 全程只看到 header，看不到 op / path / query / answer。

### 5.7 清理
```bash
cd ~/Documents/大三/CNL/cnl-final
rm -rf _test
# 終端 ①②③ Ctrl-C
```

---

## 6. 兩台機器部署（跨網際網路）

需要第三方 relay（兩邊都連得到的機器，例：`140.112.30.183:9000`）。
詳細 setup 見 [`setup.md`](setup.md)。

最短版：
```bash
# relay 機器（一次性）
python3 relay_server.py --port 9000

# 兩台各跑一次拿公鑰並交換
./script/run_A.sh      # 印 A 公鑰
./script/run_B.sh      # 印 B 公鑰

# 電腦 A（收訊）
./script/run_A.sh <B公鑰> Bob common      # 加 B 進白名單 + 啟動

# 電腦 B（送訊）
./script/run_B.sh <A公鑰> ask "你最喜歡哪本書?"
./script/run_B.sh <A公鑰> repl              # 互動模式
./script/run_B.sh <A公鑰> read   read-only/notes.md
./script/run_B.sh <A公鑰> append "read&append/log.md" "一行字"
./script/run_B.sh <A公鑰> list
```

`./script/run_A.sh` 會自動：建立 `share/` 四個 zone、檢查 Ollama daemon、列已裝模型、顯示信任白名單。

---

## 7. CLI 參考（`p2p_node.py`）

| 旗標 | 用途 |
|---|---|
| `--port` | 本機監聽 port（必填） |
| `--key-file` | X25519 私鑰檔（預設 `linkedout_<port>.key`） |
| `--name` | 顯示用暱稱（也是 ask 時對 AI 介紹的 owner 名稱） |
| `--agents-file` | agent list / 信任白名單檔（預設 `agents.json`） |
| `--trust` | 額外臨時信任的公鑰，逗號分隔（不寫檔） |
| `--share` | 本機分享資料夾（預設 `share/`） |
| `--server-ip` `--server-port` | Relay 模式：relay server 位址 |
| `--peer-pubkey` | 對方的公鑰（送訊／REPL／autonomous 必填）；`--auto` 可用逗號分隔多人做群組討論 |
| `--op` | `read` / `append` / `list` / `ask` / `capability` |
| `--path` | 操作目標（含 zone，例：`read-only/notes.md`） |
| `--content` | append 內容（支援 `\n` 換行、`\t` Tab） |
| `--query` | ask 的問題（自然語言） |
| `--mode` | ask 模式：`remote`（預設） / `local` |
| `--model` | ask 用的 Ollama 模型；不指定走 `LINKEDOUT_MODEL` / 自動偵測 |
| `--repl` | 進入 REPL 互動模式 |
| `--auto` `--goal` | autonomous 模式：本機 AI 從 goal 自己想問題、送 peer、收答案 |
| `--rounds` | autonomous 最多輪數（預設 1；>1 啟用 follow-up，LLM 自己決定何時收尾） |
| `--peer-ip` `--peer-port` | 直連模式（同 LAN，不經 relay；現已支援雙向 RESPONSE） |

---

## 8. 私密檔案（已在 `.gitignore`，請勿上傳）

| 檔 | 說明 |
|---|---|
| `*.key`、`linkedout_*.key` | 私鑰（mode 0600） |
| `agents.json` | 個人 agent list / 白名單 |
| `share/` | 個人分享資料夾 |
| `_test/` 等實驗目錄 | 本地測試殘留 |

---

## 9. NAT / P2P 現況

跨網際網路目前以 **relay 中繼** 為主。曾以 UDP 打洞測試直連 —— 校園網與行動網路（CGNAT）皆為 **symmetric NAT**，
無法穿透 → 維持 relay（等同 WebRTC ICE 在無法打洞時退回 TURN 的行為）。
**同一 LAN 可用直連模式達成真 P2P**，現已支援完整 QUERY → RESPONSE 來回。

---

## 10. 進度

- [x] E2EE（X25519 Noise-X + ChaCha20-Poly1305 + AAD 綁定）
- [x] 信任白名單（持久化 `agents.json`，fail-closed）
- [x] Per-peer tier ACL（common / task / personal）
- [x] share/ 四區 + 路徑安全
- [x] REQUEST/RESPONSE 完整 round-trip（含 id 對應）
- [x] read / append / list 操作
- [x] `ask` 操作（remote / local 兩模式）
- [x] REPL 互動模式
- [x] Autonomous 單輪（B 的 AI 自己想問題去問 A 的 AI）
- [x] Autonomous 多輪（B 看回答決定追問 / 收尾，達上限自動 summarize）
- [x] Autonomous 群組討論（`capability` op + 平行能力探測 + 主持人選人追問 + 多 peer 合成）
- [x] 跨節點聯合檢索（capability scan + targeted query — proposal 的 federated retrieval 雛形）
- [x] 直連模式雙向 RESPONSE（同 LAN 可不經 relay）
- [ ] 大 vault 的真實 retrieval（目前是把整個 zone 全塞進 context）
- [ ] NAT 穿透（已驗證打洞在 CGNAT 走不通，先放著）
