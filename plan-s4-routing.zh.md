# S4 規格：顯式知識路由協定（ROUTE_QUERY / ROUTE_ANSWER）

> 目的：讓 query 沿**信任圖多跳**找到「發問者搆不到的人」，答案**沿信任鏈回傳**。
> 對得起 proposal 標題 *A Decentralized Routing Protocol*，用掉現成 dormant 的 `ttl`/`hops`。
> 這份是**實作前的規格**：先講好封包與每節點演算法，等 Felicity push 後照抄即可。

## 為什麼要顯式協定（而非藏在答覆裡）
稀疏信任圖下：發問者 Bob **定址不到** Dave、也會**拒收** Dave 的封包。
所以要一個明確的「轉發 + 沿鏈回傳」協定，讓中間人 Carol 代為到達 Dave、再把答案帶回。
（這也是團隊選的方向：看得見的路由協定，networking 貢獻最明確。）

## 核心原則
1. **每一跳都是一段獨立的 E2EE 訊息**：Carol→Dave 是「Carol 用 Dave 公鑰加密」的新封包。
   relay 照舊只看 `target_pubkey`、看不到內容。沒有人能跳過信任關係直接送/收。
2. **答案沿信任鏈回傳**（不直送 origin）：Dave→Carol→Bob，每段都在「互信互有金鑰」的一對之間。
3. **每跳 tier 把關**：節點只把 query 轉給「自己願意對這題暴露」的信任 peer；對方也只在其 tier 視野內自評/作答。
4. **要轉給誰 = 遞迴 `capability_probe`**：節點答不全時對**自己的**信任名單做 capability 探測，命中的才轉。零維護表。

## 封包（app-layer payload，包在現有 E2EE envelope 內）
沿用 `ProtocolPacket`（外層 type 新增兩種），payload 解密後是：

### ROUTE_QUERY
```jsonc
{
  "qid":   "原點產生的唯一查詢 id",     // 去重 + 回傳對應
  "query": "自然語言問題",
  "ttl":   2,                          // 剩餘可轉跳數；每轉一跳 -1
  "path":  ["<origin8>", "<hop8>"]      // 已經過的節點（防迴圈 + provenance）；存公鑰前綴或全鑰
}
```
### ROUTE_ANSWER
```jsonc
{
  "qid":     "對應的查詢 id",
  "answer":  "這一支找到的（部分）答案文字",
  "via":     ["<dave8>", "<carol8>"]   // 這個答案經過誰（provenance，往回累積）
}
```
> schema 放 `app_layer.py`（目前乾淨、可先做）：新增 `RouteQuery` / `RouteAnswer` pydantic model
> 與 `make_route_query()`，比照現有 `FileRequest/FileResponse` 風格。

## 每節點演算法（收到 ROUTE_QUERY）
```
on ROUTE_QUERY(qid, query, ttl, path)  從上游 U 收到：
  1. 去重：qid 看過 → 丟棄。否則記 seen[qid]=now，且 route_back[qid]=U（回傳要送回 U）
  2. 自評：capability_probe(自己 vault, query)
        relevant → answer(query) → 往 U 送 ROUTE_ANSWER(qid, 答案, via=[自己])
  3. 轉發：若 ttl>0：
        候選 = 自己信任名單中，(a) 不在 path、(b) tier 夠格暴露這題 的 peer
        （可選）先對候選做 capability_probe，只轉給命中的（targeted；省訊息）
        對每個候選送 ROUTE_QUERY(qid, query, ttl-1, path+[自己])
  4. route_back[qid] 設定逾時清理（避免表無限長）

on ROUTE_ANSWER(qid, answer, via)：
  U = route_back[qid]
  若 U 存在（我是中間人）→ 把這個 ROUTE_ANSWER 轉給 U，via 追加自己
  若我是 origin → 收集進這個 qid 的答案池（見下）
```
- **answer + forward 可同時**：節點自己有部分答案也照樣往下轉，盡量蒐集多來源。
- **迴圈防止**：`path`（= 概念上的 `hops`）+ `qid` seen。**深度**：`ttl`。

## origin 端（聚合）
- origin 發 ROUTE_QUERY 給自己的直接朋友後，開一個 `qid → 答案池` + timeout（沿用現有 `self.answers` 機制擴成「可多筆」）。
- 時間內收到的多個 ROUTE_ANSWER → 去重 → 丟給 `group_summarize` 整合（附 via provenance：「CUDA 11.4，via Carol→Dave」）。

## 與現有程式的接點（哪段碰哪個檔）
| 部分 | 檔案 | 狀態 |
|---|---|---|
| `RouteQuery`/`RouteAnswer` schema + `make_route_query` | `app_layer.py` | **乾淨，可先做** |
| `handle_incoming` 新增 ROUTE_QUERY / ROUTE_ANSWER 分支、`route_back` 表、轉發迴圈 | `p2p_node.py` | ⚠️ Felicity 未 push，**等她** |
| 聚合（qid→多答案 + timeout）、origin 觸發 route | `p2p_node.py` | ⚠️ 等她 |
| 自評/作答/整合 | `ai_client.py`（`capability_probe`/`answer`/`group_summarize`） | **重用，幾乎不改**；若要改也等她 |
| relay | `relay_server.py` | **不動**（仍照 target_pubkey 轉發單跳） |

→ 可立即動的只有 `app_layer.py` 的 schema；其餘等 Felicity push 後 rebase 再實作。

## Testbed 驗收（scenario D）
- 跑 `scenario/D_gpu`：v1 = 2/3（搆不到 Dave）。
- S4 後：Bob 對「驅動」這條發 ROUTE_QUERY(ttl=2) → Alice/Carol 自評（Carol 不懂驅動但 ttl>0）→ Carol 對自己朋友探測命中 Dave → 轉給 Dave → Dave 答 CUDA 11.4 → Carol→Bob 帶回。
- 預期分數 **2/3 → 3/3**；終端可印 `via: Bob→Carol→Dave`。
- 反例：`ttl=0` 或把 Carol↔Dave 信任拿掉 → 仍 2/3（證明是「多跳」帶來那 1 分）。

## 邊界 / 取捨
- **多跳機密**：路徑上的信任轉發者讀得到 query（要自評就得讀）。用 TTL 小（1–2）+ 只往夠 tier 的 peer 收斂暴露面。
- **零答案**：timeout 後 origin 就用手上的部分答案收尾（標明「驅動那條查無人可答」）。
- **訊息量**：純 flood 會吵 → 步驟 3 的「先 capability_probe 候選再轉」是 targeted 化，之後 S5 的學習式 peer-model 再進一步省。
