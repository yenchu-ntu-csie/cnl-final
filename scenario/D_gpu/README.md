# Scenario D — 跨領域專業 testbed

量「agent 協作 / 多跳路由到底有沒有實際幫助」的可重現測試場景。

## 為什麼這樣設計
- **知識分散在不同專業的人手上**（不是可以丟進一個共享文件的死資料）→ 單一 agent 答不全。
- **金鑰圖稀疏**：你只跟少數人交換過金鑰，圈外的人**定址不到**，只能經共同朋友轉（= 需要 next hop 的真正原因）。
- **有 ground truth**（3 條關鍵事實）→ 客觀打分，能比較 baseline vs 協作 vs 多跳。

## 設定
情境：把實驗室那台舊 GPU 機器改成「在家也能安全連進去」的本地 LLM 服務。四個專業：

| agent | 領域 | 只有他知道的關鍵事實（刻意是通用 LLM 猜不到的私有值） | 金鑰圖 |
|---|---|---|---|
| Bob(問) | 應用/部署 | （無；他要問人） | 有 Alice、Carol |
| Alice | ML/量化 | 這台實測：Q4_K_M + context 上限 **3500** | Bob 的朋友 |
| Carol | 網路 | Tailscale tailnet **lab-7f3a**、防火牆只開 UDP **41641** | Bob 的朋友 |
| Dave | 系統/硬體 | K40 鎖 **CUDA 11.4** + BIOS 關 **Above 4G Decoding** | **只有 Carol 認識** |

GT = 3 條關鍵事實；分數 = 最終答案命中幾條。
**重點**：關鍵字刻意選「私有特定值」（3500 / lab-7f3a / 41641 / 11.4 / Above 4G）——
通用 LLM 沒被告知就猜不出來，所以 baseline 拿不到分，只有真的問到對的人才補得上。

## 跑法
```bash
# 在 repo 根目錄、已建 .venv、ollama 在跑
.venv/bin/python scenario/D_gpu/run_scenario.py
```
純 Python orchestrator（不依賴特定 shell；跨平台）。它會：
1. 建 4 個 agent 的 share/ 種子 + 金鑰 + 稀疏金鑰圖（全在 `run/`，gitignored）
2. 起 relay + Alice/Carol/Dave
3. 跑 **baseline**（Bob 只用自己 vault）與 **v1**（Bob `--auto` 群組：capability scan + 問直接朋友）
4. 各自對 GT 打分、印對照

## 預期結果
| | 分數 | 說明 |
|---|---|---|
| baseline | ~0/3 | Bob 自己猜不到任何私有特定值 |
| v1（問直接朋友） | **2/3** | 拿到 Alice（3500/Q4_K_M）+ Carol（lab-7f3a/41641）；**缺 Dave（11.4/Above 4G），因為 Bob 搆不到 Dave** |
| 多跳 S4（已實作） | 目標 3/3 | Carol 代轉到 Dave，補上驅動 |

→ 缺的那 1/3 = **「為什麼需要多跳」的可量化證據**。

**S4 已實作**（`p2p_node.py` 的 `ROUTE_QUERY`/`ROUTE_ANSWER` + `route_ask`，CLI `--route --ttl`）。
跑 S4 對照：`.venv/bin/python scenario/D_gpu/run_s4_demo.py`（baseline vs v1 vs S4）。
傳輸已驗證正確（Bob→Carol→Dave 送達、2 跳回程 Dave→Carol→Bob）；S4 也套用 **每跳 tier ACL**
（answering 端只用「對直接上游的 tier」作答，common 經路由也拿不到 personal）。

## 檔案
- `seeds/<who>/read-only/*.md` — 各 agent 的專業種子資料
- `ground_truth.json` — 3 條關鍵事實 + 比對關鍵字
- `score.py` — 把一段答案對 GT 打分（可獨立用：`python score.py <檔> --label x`）
- `run_scenario.py` — orchestrator
- `run/` — 跑起來的工作區（gitignored）
