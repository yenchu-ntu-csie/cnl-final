# 下一步規劃 —— 三層權限 ACL（Tier-based ACL）

> 這份計畫是給隊友看的中文版；對應的英文原稿在我（diaaaaaaaaaaaaan）本地 `~/.claude/plans/linkedout-next-steps.md`，內容一致。

## 現況

- 分支：`feat/ollama-ask`，目前比 `main` 多 4 個 commit，全都已 push。
- 寫計畫前剛重抓 `origin/main`，**沒有新東西**（最後一個 commit 還是 `29f031b 📖`，那次我們已經合進來）。動工前會再 fetch 一次以防同步落後。
- 目前這條分支上完成的內容：
  - `ask` op：透過本地 Ollama 回答對方的問題，完整走 E2EE / relay / REQUEST-RESPONSE 流程。
  - AI 回應為純字串（不再多包一層 JSON）。
  - REPL 模式（`--repl`）：使用者可以坐在 `linkedout>` 提示字元輸入問題，不用每次重啟 process。
  - 不在程式碼裡 hardcode 任何模型名稱。`ai_client.resolve_model()` 依序找：`--model` 旗標 → `LINKEDOUT_MODEL` 環境變數 → `OLLAMA_MODEL` → 本機 `ollama list` 第一個。
  - 已合併同學更新的 README + 移除 `holepunch/`。

未完事項都記在 `cnl-final/todo.md`。這份計畫只挑下一步要動的事，不一次規劃完所有。

---

## 協作守則（這是團體專案，下面每一項都適用）

1. **不要 hardcode 任何「只在我電腦上有的東西」**
   模型名稱、IP、hostname、port、檔案路徑、使用者名稱、API key —— 全部都要透過 CLI 參數或環境變數，並有合理的自動 fallback。
   （Ollama 模型那次我們已經用 `resolve_model()` 落實過，同樣的紀律要延續下去。）
   *Review 時可以直接 grep 新 commit 裡的字串字面值，看看有沒有「聞起來很本地」的東西。*

2. **避免跟隊友互踩、減少 merge conflict**
   - **加東西，不要重排版。** 擴 `agents.py`、`app_layer.py`、`p2p_node.py` 時，**加新函式 / 新 CLI 子指令**就好，不要重寫或重排既有函式、不要動 argparse 既有參數的順序。
   - **共用 CLI 形狀不要改名/改語意。** `script/run_A.sh`、`script/run_B.sh` 已經有固定旗標（`--op`、`--path`、`--content`、`--peer-pubkey`）。新旗標必須是「加上的、optional 的」；既有旗標的名稱跟行為**不可以**改。
   - **常數集中宣告一次。** Zone 名稱（現有的 `ZONE_READONLY`、`ZONE_APPEND` 跟這次新加的兩個）只在 `app_layer.py` 開頭宣告一次，其他地方一律 import 來用 —— **絕對不要**到處重打字串字面值。
   - **push 前先 fetch 再 merge。** 每次 push 前先 `git fetch origin main && git log feat/ollama-ask..origin/main`；若有東西就先合進來再 push，最終 PR 才會乾淨。
   - **README 不要碰**（除非當下有明確共識）。README 由那位同學負責；我們負責 `setup.md` 跟 `todo.md`。

---

## 這次要做的：每位 peer 的權限分層（Tier ACL）—— 對應 todo.md §2

### 為什麼挑這個？

- **Proposal 招牌圖**是 Personal / Task / Common 三層同心圓。現在所有 peer 一律是 `Common`，完全沒展示分層。
- **基礎管線已經接好**：
  - [`P2PNode.agent_meta`](p2p_node.py) 已經帶著整個 dict
  - [`app_layer._do_ask`](app_layer.py) 已經接受 `tier` 參數
  - [`handle_request`](app_layer.py) 也已經一路傳下去
  - 唯一缺的，就是 `agents.json` 裡**沒有 tier 欄位可以讀**。把這個欄位補進去，整套就活了。
- 一次同時收斂 `ask`、`read`、`list`、`append` 四個 op 的權限邊界，CP 值最高。
- IDE 訊號：使用者剛剛打開 `cnl-final/agents.py`，方向一致。

### 設計

**Tier 值：** `"common"`（預設 / 所有 agent list 裡的人）< `"task"` < `"personal"`。
**用字串不用整數**，因為要跨 JSON。
**只在 `agents.py` 模組頂端宣告一次** `TIERS = ("common", "task", "personal")`，`app_layer.py` 用 `from agents import TIERS` 引用 —— 不在多個地方各寫一次字串（協作守則 2.c）。

**儲存：** 擴 `agents.json` 每筆 entry 從 `{name, added}` 變成 `{name, added, tier}`。`tier` 是 optional，沒有就視為 `"common"`。**向下相容**：舊的 `agents.json` 不會壞。

**`agents.py` CLI 變動：**
- `python3 agents.py add <pubkey> --name B --tier task`
- `python3 agents.py set-tier <pubkey> <tier>`（新指令，不必重 add 就能改）
- `python3 agents.py list` 多印一欄 tier
- 寫入時驗證 tier 必須在 `TIERS` 裡，不在就 raise。

**`share/` 結構：** 新增 `share/task/` 和 `share/personal/` 兩個分區。對應關係：

| Peer tier | 看得到的分區 |
|---|---|
| `common`   | `read-only/`、`read&append/` |
| `task`     | 上面 + `task/` |
| `personal` | 上面 + `personal/` |

`app_layer` 既有的 `read`/`append`/`list` 路徑檢查（`_check`、`_safe_resolve`）保留不動 —— 只在「判定 zone 屬於哪一層」的那一段加上 tier 過濾。`_collect_ask_context`（給 LLM 看的素材）也比照 tier 過濾要走哪些資料夾。

**`ensure_share()` 改動：** 啟動時**四個 zone 都自動建立**（user 已同意採用這個方案 —— 對新隊員比較友善，也直接符合 proposal 那張圖）。

### 動到哪些檔（含 conflict 風險）

| 檔案 | 改什麼 | Conflict 風險 |
|---|---|---|
| `agents.py` | 加 `TIERS` 常數；`add(...)` 多吃 optional `tier`；新增 `set_tier(...)`；CLI 加 `set-tier` 子指令；`_print_list` 多印一欄。**全部 additive**：舊的 `add(pubkey, name)` 呼叫照樣能跑（`tier=None` 預設）。 | 低 —— 檔案小、寫的人不多。 |
| `app_layer.py` | 新增 `_zones_for_tier(tier)`；`_check` 跟 `_do_list` 多吃 optional `tier`（預設 `"common"`）；`_collect_ask_context` 按 tier 過濾走訪。在現有 `ZONE_READONLY` / `ZONE_APPEND` 旁邊加兩個新 zone 常數。 | 中 —— 檔案最大。改動**只動 zone 判定那一段**，**不要**重排周邊程式碼。 |
| `p2p_node.py` | 已經完全接好（`self.agent_meta[sender].get("tier", "common")` 直接能讀）。**預計不用動**；除非實作時發現少接哪一條。 | 極低 —— 大概不會動。 |
| `setup.md` | 加一節說明三層權限怎麼設。 | 無 —— 我們負責。 |
| `todo.md` | 把 §2 打勾、移到完成區。 | 無 —— 我們負責。 |
| `README.md` | **不動**。同學負責。 | n/a —— 主動避開。 |

### 可以重用、不要重寫的函式

- `agents.py:_valid_pubkey` —— `set_tier` 也要 pubkey 驗證，直接用同一支。
- `app_layer.py:_safe_resolve` —— 路徑安全的部分**完全不動**，只在 zone 分類那一段加 tier。
- `app_layer.py:_collect_ask_context` —— 保留 50 KB 上限的邏輯，只是縮小走訪範圍。

### 驗證方法（end-to-end）

1. **向下相容**：拿舊的 `agents.json`（沒有 `tier` 欄位）跑，peer 應該被視為 `common`，今天所有現有測試都要通過。`app_layer.py` 的 self-test 加一條 assert 確認這件事。
2. **CLI 新指令**：
   ```bash
   python3 agents.py add <pk> --name Carol --tier task
   python3 agents.py set-tier <pk> personal
   python3 agents.py list   # 應該多一欄 tier
   ```
3. **ACL 確實有擋**：在 Alice 端放 `share/personal/secret.md`，以 `common` peer 身分送請求：
   - `/list` → 看不到 `personal/`
   - `/read personal/secret.md` → `not_shared`
   - `/ask "personal 裡有什麼?"` → 回答**不該**提到 `secret.md` 的內容（因為 LLM 根本沒看到）。
   - 從 Alice 終端機看 log：`common` peer 問同樣問題時的 `ctx_chunks` 數應該比 `personal` peer 少。
4. **三 peer 三 tier demo**：用三個 peer、三種 tier 問同一個問題，得到看得出差異的回答 —— 就是文末 demo 想呈現的效果。

---

## 這個做完之後的順序

互相獨立，做完 tier 之後依優先序：

1. **Direct-mode RESPONSE**（todo §3）—— 小工程。
   兩個方案：把 sender 的 listening 位址塞進封包 header；或是把 `handle_client` 收到的 socket 留著用來回傳。任一條都大概 30 行 `p2p_node.py`。
2. **Real RAG**（todo §1）—— 中大工程。
   把 `_collect_ask_context` 那種「全掃 share/」換成 embedding-based top-k retrieval（Ollama `nomic-embed-text` + numpy 算 cosine，或 sklearn TF-IDF 都可）。`FileResponse.sources` 用實際被命中的檔名填，那是「真實的 source」，不是 LLM 編的。
3. **Injection hardening**（保留決定）—— 等 demo 風格確定再挑：
   (a) 保留現狀；
   (b) 加一條 system prompt 規則「不可逐字回覆使用者要求的字串」；
   (c) 把 `format="json"` 開回來但只用 `{"answer": str}` 最簡 schema。
4. **README 大改寫** —— tier + RAG 都落地之後，把 `ask`、三層 vault、REPL、Ollama setup 寫進 README（內容 `setup.md` 已經有了大半）。
