## May 20
可跨WiFi通訊
### Server
`python3 relay_server.py --port 9000`
### Peer A
```
python3 p2p_node.py --port 8001 --pubkey 0xNODE_A --server-ip 140.112.30.188 --server-port 9000

🌐 [Mode] Relay 模式（Server: 140.112.30.188:9000）
🔗 [Relay] Connecting to 140.112.30.188:9000 ...
✅ [Relay] Registered as 0xNODE_A
   👥 Online: ['0xNODE_A']
📥 [Relay] From: 0xNODE_B | Type: QUERY
   🧠 [Mock AI] 正在解析 Query... 假裝思考了 2 秒
   🧠 [Mock AI] 回應 Payload: {'query_text': 'Hi from B'}
```
### Peer B
```
python3 p2p_node.py --port 8002 --pubkey 0xNODE_B --server-ip 140.112.30.188 --server-port 9000 --peer-pubkey 0xNODE_A --message 'Hi from B'
🌐 [Mode] Relay 模式（Server: 140.112.30.188:9000）
🔗 [Relay] Connecting to 140.112.30.188:9000 ...
✅ [Relay] Registered as 0xNODE_B
   👥 Online: ['0xNODE_A', '0xNODE_B']
📤 [Relay] Sent to 0xNODE_A
```

## TODO
* 串接 API / JSON 格式
* 傳輸安全 (EE2E / mTLS / Public Key)
* 檔案權限（暫時人工分類）
* Agent
* 改 P2P 