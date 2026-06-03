# LinkedOut 5 機多跳 Demo Runbook

> 目標：5 台機器、真‧跨機多跳知識路由(S4)，錄影展示 **(1) 多跳路徑可視化** 與 **(2) tier 分層揭露**。
> 內容沿用 scenario D（舊 GPU 機器改本地 LLM 服務）。relay 跑在第 5 人那台。

## 0. 角色與金鑰圖（稀疏 → 逼出多跳）
| 角色 | 誰 | 公鑰(前綴) | vault 裡那條私有事實 |
|---|---|---|---|
| **Bob**(發問) | （我） | `9e5e5623…` | 無（他要問人） |
| **Alice**(ML) | | `cb9e691f…` | `Q4_K_M 量化 + context 上限 3500`（放 read-only/） |
| **Carol**(網路) | Felicity | `e7648cbd…` | `Tailscale tailnet lab-7f3a + 防火牆只開 UDP 41641` |
| **Dave**(硬體) | | `（待補）` | `K40 → 鎖 CUDA 11.4 + BIOS 關 Above 4G Decoding` |
| **Relay** | 第 5 人 | — | 跑 `relay_server.py`（不需 ollama / vault） |

**金鑰圖（誰加誰 = 誰能定址誰）**
- Bob 的 agents.json：Alice、Carol（**沒有 Dave** → Bob 搆不到 Dave，這就是多跳的理由）
- Carol：Bob、**Dave**（只有 Carol 認識 Dave）
- Alice：Bob　Dave：Carol
- → Dave 的「CUDA 11.4」這條只能 `Bob → Carol → Dave` 兩跳拿到。

## 1. 每個人都先做（一次）
```bash
cd cnl-final
git pull                      # 確保大家同版本（含 S4 路由 + store-and-forward）；★沒更新→多跳會斷
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# ollama：各自用現有模型即可。確認有跑、有模型：
ollama list                   # 沒有就 ollama pull qwen2.5:7b（或你機器跑得動的）
```

## 2. 產生自己的身分、交換公鑰
```bash
python3 -c "import e2ee; print(e2ee.public_hex(e2ee.load_or_create_identity('node.key')))"
# 把印出的 64 字公鑰貼到群組；收齊大家的公鑰
```

## 3. 按金鑰圖把該加的人加進白名單
```bash
# 例：Carol 要加 Bob 和 Dave
python3 agents.py add <Bob的公鑰>  --name Bob
python3 agents.py add <Dave的公鑰> --name Dave
python3 agents.py list             # 確認
```
（tier 預設 common；事實放 read-only/ 即可被 common 讀到。）

## 4. 放自己那條事實
```bash
mkdir -p share/read-only
echo "<你那條私有事實>" > share/read-only/note.md
```

## 5. 啟動節點（連 relay）
```bash
# 收訊方（Alice / Carol / Dave）— 連 relay、等問題
python3 -u p2p_node.py --port 8001 --name <你的角色> \
  --key-file node.key --server-ip <RELAY_IP> --server-port 9000
```
- **Relay 那台**：`python3 -u relay_server.py --port 9000`（記下對外 IP 給大家）

## 6. Bob 發起多跳查詢（錄影主秀）
```bash
python3 -u p2p_node.py --port 8001 --name Bob \
  --key-file node.key --server-ip <RELAY_IP> --server-port 9000 \
  --route --goal "我要把實驗室那台舊 GPU 機器改造成本地 LLM 服務，並讓我在家也能安全連進去用。關鍵設定？" \
  --ttl 2 --window 300
```
**畫面會看到（錄影重點）：**
- `origin 發 qid=… 給 N 個直接朋友（cold-flood）`
- 各跳 `🧭 [Route] 收到…`、`Carol ttl=2→1 轉發給 1 個 peer`、`Dave 有料 → 回 ROUTE_ANSWER`
- 最終整理 + **🔎 揭露稽核 provenance**：每條答案的 `path: Bob → Carol → Dave`、`tier=common`、片段
- 三條事實(3500 / lab-7f3a,41641 / CUDA 11.4)都進最終答案 → 證明多跳補上了 Bob 搆不到的 Dave。

## Pre-flight 檢查（錄影前 dry-run 一次）
- [ ] 5 人都 `git pull` 到同一版（`git log -1` 比對 hash）
- [ ] relay 起來、大家 `✅ Registered`、Bob 看得到完整 online 清單
- [ ] 金鑰圖正確（Bob 沒加 Dave；Carol 有 Dave）
- [ ] 各收訊方 ollama 有跑、有模型
- [ ] 先小跑一次 `--route`，確認 2 跳 `Bob→Carol→Dave` 通、provenance 印得出來

## 疑難排解
- **多跳沒到 Dave / 答案缺 CUDA 11.4**：通常是 (a) 有人沒 `git pull`(舊版無 S4)；(b) 模型慢、答案在窗關後才回 → Bob 加大 `--window`(如 400~600)；(c) Dave 的 capability 自評說「不相關」→ 用強一點的模型 / 把事實寫清楚。
- **對方暫時離線**：relay 現在會**暫存**、對方上線自動補投（不會丟訊息）。
- **被拒收**：對方白名單沒加你 → 互相 `agents.py add`。

## 想加 #2 之外的 tier 隱私小段（可選）
把某條事實改放 `share/personal/`，common tier 的 Bob 路由會**拿不到**（provenance 看不到那條）；
把該 peer 的 tier 調成 personal（`agents.py set-tier <pub> personal`）再跑，就拿得到 → 對照出 tier ACL。
