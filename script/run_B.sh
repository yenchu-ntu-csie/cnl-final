#!/usr/bin/env bash
# ============================================================
# 電腦 B —— 送訊方 (sender)
#
# E2EE 必須知道對方公鑰才能加密，所以送訊方一定要指定要送給誰。
# 對 A 的 share/ 做 read / append，A 會把結果加密回傳。
# path 要含 zone：read-only/...（只能讀）或 read&append/...（可讀可追加）
#
# 用法（注意 read&append 含 & 要用引號）：
#   ./run_B.sh                                                 # 只印出自己的公鑰
#   ./run_B.sh <A的公鑰> read   "read-only/notes.md"           # 讀 A 的唯讀區
#   ./run_B.sh <A的公鑰> append "read&append/log.md" "一行字"   # 追加到 A 的可寫區
#
# Relay IP 可用環境變數覆蓋：RELAY_IP=1.2.3.4 ./run_B.sh <A的公鑰> read "read-only/notes.md"
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."   # 切到專案根目錄（e2ee.py / p2p_node.py 所在）

# ===== 設定（依需要修改；要跟 A 連同一台 relay）=====
RELAY_IP="${RELAY_IP:-140.112.30.183}"
RELAY_PORT="${RELAY_PORT:-9000}"
PORT=8002
KEYFILE="linkedout_${PORT}.key"

# ===== 顯示自己的公鑰 =====
MY_PUB=$(python3 -c "import e2ee; print(e2ee.public_hex(e2ee.load_or_create_identity('$KEYFILE')))")
echo "============================================================"
echo "🔑 這台 (B) 的公鑰 —— 複製給 A："
echo "   $MY_PUB"
echo "============================================================"

PEER_PUBKEY="${1:-}"
OP="${2:-read}"
FILEPATH="${3:-read-only/notes.md}"
CONTENT="${4:-}"
if [ -z "$PEER_PUBKEY" ]; then
  echo "ℹ️  還沒收到 A 的公鑰。把上面的公鑰傳給 A，拿到 A 的公鑰後再執行："
  echo "      ./run_B.sh <A的公鑰> read   \"read-only/notes.md\""
  echo "      ./run_B.sh <A的公鑰> append \"read&append/log.md\" \"要追加的內容\""
  exit 0
fi

# 把 A 加進白名單（之後 A 回訊 / RESPONSE 也會被信任）
python3 agents.py add "$PEER_PUBKEY" --name A

echo "📨 對 A (${PEER_PUBKEY:0:16}…) 執行：op=${OP} path=${FILEPATH}"
echo "🌐 Relay: ${RELAY_IP}:${RELAY_PORT}"
echo

exec python3 -u p2p_node.py --port "$PORT" --name B \
  --server-ip "$RELAY_IP" --server-port "$RELAY_PORT" \
  --peer-pubkey "$PEER_PUBKEY" --op "$OP" --path "$FILEPATH" --content "$CONTENT"
