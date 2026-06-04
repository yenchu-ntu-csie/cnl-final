# LinkedOut 報告素材（架構 / 結果 / 限制）

> 給寫報告 / 投影片用的彙整。數據以實跑為準（見各 testbed）。

## 一、定位（一句話）
**個人 AI agent 的去中心化「知識路由」協定**：你問一題、不必知道誰懂；查詢沿你的信任圖
（可多跳）找到對的人的 agent 回答，答案沿信任鏈帶回，全程 E2EE、分層揭露、可顯示來源路徑。

## 二、分層架構
```
 應用層 (app_layer)   ask(remote/local) · read/append/list · capability · tier ACL(common<task<personal)
 自主/路由層 (p2p_node) autonomous 群組(--auto, 平行 capability scan)
                       S4 知識路由(--route): ROUTE_QUERY/ROUTE_ANSWER 多跳 + 聲望 targeted
                       中介 agent: 每個轉發節點收下游答案 → 以「對上游 tier」metadata 硬擋過濾 + 忠實彙整再回
                       (回程也把關 tier；深層來源匿名成「轉述 N 位聯絡人」；最終 synthesis 由 origin 做一次)
 加密層 (e2ee)        X25519 + ChaCha20-Poly1305(AEAD)，每訊息臨時金鑰，metadata 綁 AAD
 中繼 (relay_server)  依公鑰轉發、看不到明文；離線存轉(queue + 上線補投)
 信任 (agents)        白名單(fail-closed) + per-peer tier + per-topic reputation(有獎有罰、會退場)
```

## 三、信任圖上的多跳路由（核心圖）
```
         ┌─ Alice (ML)           Bob 沒有 Dave 的金鑰
 Bob ────┤                        → 「CUDA 11.4」只能兩跳拿到：
 (問)    └─ Carol (網路) ── Dave (硬體)        Bob → Carol → Dave
              ↑ 每一跳都在「互信、互有金鑰」的一對之間；每跳用上游的 tier 把關
   答案沿信任鏈反向回傳：Dave → Carol → Bob（origin 拒收陌生人，故不直送）
   回程不是盲轉：Carol 是「中介 agent」，把 Dave 的答案 + 自己的料以 tier 過濾後忠實彙整再回 Bob。
```
- **要不要問 / 問誰 / 答不答**＝同一個本地判斷「給這題 + 我的 vault，我有沒有能貢獻的」。
- **next hop 為何需要**：金鑰圖稀疏 → 圈外的人定址不到，只能經共同朋友轉（≈ IP 經 gateway）。
- **中介 agent（非無腦轉發）**：每個轉發節點收到下游回覆會以「自己對上游的 tier」**過濾並彙整**再上送：
  ① 回程也把關 tier——用 **metadata 硬擋**（丟掉 tier 高於上游的內容），比 LLM 軟過濾可靠，
  深層專家依「他信任中介」吐的內容**不會越權流回低 tier 的 origin**；② 深層來源對上游**匿名**
  （Bob 只看到「Carol 轉述 N 位聯絡人」，看不到 Dave）；③ 中介層**不再呼叫 LLM**（忠實保留具體值、
  不逐跳改寫流失事實、在慢模型上也不會多一次 timeout）→ 唯一的最終 synthesis 由 origin 做一次。

## 四、主要結果（實跑；數字見 testbed 輸出）
1. **協作 vs 單打（scenario D，per-fact 命中率）**：baseline（自己/通用 LLM）對私有特定值（3500 / lab-7f3a / 41641 / CUDA 11.4）≈ **0**；問直接朋友拿到 2 條；**只有多跳(S4) 能補上第 3 條（Dave 的 CUDA 11.4）** → 證明多跳帶來的價值非隨機。
2. **智慧路由收斂（run_converge，Bob 接 4 友、只 Carol 通往答案）**：同主題重複問 →
   | run | origin fan-out | mode | 命中 CUDA 11.4 |
   |---|---|---|---|
   | 1 | **4** | cold-flood | ✅ |
   | 2 | **2** | targeted+explore | ✅ |
   | 3 | **2** | targeted+explore | ✅ |
   → 從回饋學到「這題往 Carol 轉」後，origin 訊息**減半(4→2)**、跳過無關的 Alice/Erin/Frank，**答案每次仍命中**。路由表收斂、訊息變少但不犧牲正確性。
3. **並行 fan-out**：capability 探測(`asyncio.gather`)與多跳查詢(scatter-gather)皆並行 → 延遲 ≈ 最慢一跳，非相加。
4. **信任度評分：有獎有罰、會退場（test_routing）**：next-hop 帶回答案 → +1；轉了卻沒貢獻 → −0.5；
   分數下限 0（降到 0 = 退場、回冷啟動）。**關鍵論述**：一般 reputation 要對陌生人打分（Sybil/冷啟動/假評價）；
   我們**只對信任名單內的朋友打分**，陌生人的知識經「打過分的朋友」流進來 → 評分有真實錨點、抗 Sybil；
   且像 distance-vector 的 metric——獎勵讓路由收斂、懲罰讓失效/過期的轉介退場。
5. **安全性（test_security 6 項全過）**：空白名單 fail-closed 拒收、未授權拒收、common 經 S4 也拿不到 personal
   （tier ACL 不被路由繞過）；**回程經中介 agent 以對上游 tier 做 metadata 硬擋 → 越權內容不會沿回程外洩**。
6. **韌性（test_storeforward）**：peer 離線 → relay 暫存、上線補投，訊息不丟。

## 五、誠實的限制（report 要寫，顯示我們知道邊界）
- **relay 信任**：relay 不可信（只見密文），但**註冊未認證** → 可被搶註冊造成 DoS（不洩內容）。屬輕量 relay 設計取捨；challenge-response 認證列為 future work。
- **forward secrecy**：Noise-X 單向模式 → 對「寄件者金鑰外洩」有 FS，但**對收件者靜態金鑰外洩沒有**（舊封包可被解）。提供的是加密 + 寄件者驗證，非完整 post-compromise FS。
- **多跳機密**：路徑上的信任轉發者**讀得到 query**（要自評才能路由）。用 TTL 小 + 每跳 tier 收斂暴露面；query-private（onion）多跳列為 future work。
- **無一般封包 replay cache**：重放偵測未做（信任白名單內風險低）。
- **LLM 隨機性**：單次 capability 自評/作答受模型品質影響（小模型 flaky）→ 用 per-fact 命中率 + n 次分佈呈現，而非單次總分。
- **路由演算法非新**：多跳/flooding/聲望收斂是經典 P2P；本專案貢獻是「**實作一個會動的、給 LLM agent 用的去中心知識路由系統**」（含 tier 揭露、provenance、學習式 targeting），不是新演算法。

## 六、不在範圍（future work）
S5 進階學習路由的更強模型、store-and-forward 的 TTL/持久化、NAT 打洞直連、relay challenge-response、
query-private onion 多跳、真 RAG（TF-IDF top-k + 真檔案來源）。
