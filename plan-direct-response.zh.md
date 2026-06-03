# 下一步規劃 —— 直連模式的 RESPONSE 回傳（todo §3）

> 給隊友看的中文版；英文原稿在我本地 `~/.claude/plans/linkedout-direct-response.md`。

## 現況 / 為什麼要做

目前 QUERY→RESPONSE 的完整來回**只有 relay 模式跑得通**。直連模式（`--peer-ip/--peer-port`，不經 relay）下：

- 收訊方收到 REQUEST、處理完、組好 RESPONSE，但**沒有通道送回去** —— `handle_incoming` 只會用 `self.srv_writer`（relay 連線）回，所以只印 `⚠️ 直連模式尚未接回應通道`。
- 送訊方的 `send_packet` 開連線、寫完 REQUEST 後**馬上關掉**，就算對方想回也沒 socket 可收。

目標：讓直連模式完成跟 relay 一樣的來回，重用既有的 `app_layer.handle_request` / `handle_response` / E2EE，**不動協定與封包格式**。

## 做法：回應走「同一條 TCP 連線」回傳（Option A）

送訊方送完 REQUEST 後**不關連線**，在同一條 socket 上等 RESPONSE；收訊方把 accept 進來的那個 `writer` 當回應通道直接寫回去。

選這個而不是「在 header 塞回撥位址、收訊方另開連線連回送訊方」（Option B），因為 Option A 不需要送訊方額外能被連入，也最像一般「一條連線、一來一回」的 request/response。

## 協作守則（同前，團體 repo）
- 只加、不重排：別改既有 CLI 旗標名稱/順序、別為了風格重寫沒壞的函式。
- 不 hardcode 本機參數（timeout 用具名常數，不要散落的魔術數字）。
- push 前先重抓 `origin/main`，有東西先合再推。
- 不要碰 README（同學負責）；說明寫進 `setup.md` / `todo.md`。

## 要改的地方（全都在 `p2p_node.py`）

1. **`handle_incoming(self, packet, reply_writer=None)`** —— 多一個 optional `reply_writer`。
   在 REQUEST 分支送 RESPONSE 時：
   - relay 模式（有 `self.srv_writer`）：照舊 `send_via_relay`
   - **直連模式（有 `reply_writer`）**：把 RESPONSE 封包 `model_dump_json()+"\n"` 寫進 `reply_writer`、`await drain()`，印一行 `📤 [Direct] RESPONSE 已回傳`
   - 兩者都沒有：保留原本的警告

2. **`handle_client(self, reader, writer)`** —— 把 accept 到的 `writer` 往下傳：
   `await self.handle_incoming(packet, reply_writer=writer)`。

3. **`send_packet(...)`** —— 寫完 REQUEST 後**不要關**，改成在同連線讀一行 RESPONSE 回來再分派：
   ```python
   line = await asyncio.wait_for(reader.readline(), timeout=DIRECT_REPLY_TIMEOUT)
   if line:
       resp = ProtocolPacket.model_validate_json(line.decode().strip())
       await self.handle_incoming(resp)   # RESPONSE → handle_response 印出來
   ```
   用 try/except 包 `asyncio.TimeoutError`（慢或沒回）與驗證錯誤；`finally` 關 writer。
   既有的「連線/送出 retry」保留，只在送出成功後**多讀一次回應**。

4. **模組常數**（跟其他可調參數放一起）：
   `DIRECT_REPLY_TIMEOUT = 200  # 秒；涵蓋慢的 ask（ollama 冷啟動）`。不要把數字寫死在行內。

5. **`main()` 直連送訊方分支** —— 結構不用改；`send_packet` 現在會把回應就地印出來。（小優化：一次性直連送完可以直接結束、不用 `while True: sleep`，但為了 diff 最小先不動，除非很 trivial。）

relay 模式完全不受影響（那條路 `reply_writer` 是 `None`，走原本的 `send_via_relay`）。

## 不在範圍內
- 直連模式的 REPL（REPL 先維持只有 relay 模式）。
- 一條連線多次來回 / streaming（單 REQUEST → 單 RESPONSE 就夠）。

## 驗證（不開 relay，兩個節點都在 localhost）
1. 語法：`.venv/bin/python -m py_compile p2p_node.py`
2. 開收訊方：`p2p_node.py --port 8001 --name Alice --share /tmp/a/share`（先 `agents.py add <Bob公鑰>`，並在 `/tmp/a/share/read-only/` 放一個檔）
3. 快的 op（不用 ollama）：送訊方
   `... --port 8002 --peer-ip 127.0.0.1 --peer-port 8001 --peer-pubkey <Alice公鑰> --op read --path read-only/notes.md`
   → 送訊方印出 `✅ read 成功` 框（RESPONSE 從同一條 socket 回來了）
4. 慢的 op：同上換 `--op ask --query "..."` → ollama 跑完後送訊方印出 `🤖 AI 回應` 框（在 `DIRECT_REPLY_TIMEOUT` 內）
5. 直連下 tier 仍生效（common 問 personal 檔 → `not_shared`），證明這次只動「傳輸」，沒動「權限」
6. relay 模式回歸測試：把既有的 relay 來回再跑一次 → 照樣 OK
