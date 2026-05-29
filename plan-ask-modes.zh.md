# 功能說明 —— `ask` 的兩種模式（已實作）

> 給隊友看的中文說明。這個功能已經做完並驗證過(remote / local 都跑過 end-to-end)。

## 一句話

A 問 B 時,可以選「**讓 B 的 AI 幫我統整答案**」或「**只跟 B 拿原始資料、我自己的 AI 來生成**」。
由 **A(送訊方)的人手動選**,透過 JSON 的 `mode` 欄位決定;**不是 AI、不是看 prompt 內容**。

## 兩種模式

| | `remote`(預設) | `local` |
|---|---|---|
| 誰跑模型 | **B(被問的人)** | **A(問的人)** |
| 傳出去的東西 | 只有「消化過的答案」 | B 的**原始 chunks**(tier 過濾後) |
| 誰要有 ollama | B | A |
| 適合 | 隱私優先;A 沒模型/弱模型 | A 想自己對資料推理、或之後要綜合多個來源 |

**對稱性**:兩種模式 B 撈的 context 一模一樣(都是 `_collect_ask_context(share, tier)`),差別只在「誰生成」。

## 重點:權限(tier)在兩種模式都生效
`local` **不會**回傳 A 的 tier 看不到的 chunks —— 跟 `read`/`list` 同一套 tier ACL。
所以「mode 由人選」不影響安全:該擋的資料在 app_layer 那層就擋掉了,不在 mode 這層重複判斷。

## 怎麼用

```bash
# remote(預設):B 的 AI 幫你統整
python3 p2p_node.py --port 8002 --name Bob --server-ip <relay> \
  --peer-pubkey <對方公鑰> --op ask --mode remote --query "你的問題"

# local:B 只回原始資料,你自己的 AI 生成
python3 p2p_node.py ... --op ask --mode local --query "你的問題"
```

REPL 裡:
- 直接打字 / `/ask` → 用啟動時 `--mode` 的預設
- `/remote <問題>` → 強制 remote
- `/local <問題>` → 強制 local

## 程式落點(都很小、additive)
- `app_layer.py`:`FileRequest.mode`、`FileResponse.context`、`ASK_MODES` 常數、`_do_ask` 依 mode 分流。
- `ai_client.py`:新增 `synthesize()` —— A 端 local 模式用「對方給的 chunks」生成(system prompt 框架是「A 在回答自己」,跟 remote 的 `answer()` 不同)。
- `p2p_node.py`:`register_pending()`(送出前記住 query)+ `_synthesize_local()`(收到 chunks 後用本機 AI 生成)+ `--mode` 旗標 + REPL `/local` `/remote`。

## 向下相容
舊的請求沒有 `mode` 欄位 → 一律當 `remote`。現有 relay / demo 完全不受影響。

## 設計決定備忘(future work 不做)
討論過「能不能讓系統**自動**選 mode(policy 規則 / router AI)」,結論:**不做**。
理由:權限規則已經在檔案 tier ACL 處理過,不需要在 mode 這層重複一套規則。維持「人手動選」最透明可控。
