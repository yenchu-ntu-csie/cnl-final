#!/usr/bin/env bash
# ============================================================
# 電腦 B —— 送訊方 (sender)
#
# 用法：
#   1) 第一次：直接執行，印出 B 的公鑰，傳給 A
#        ./run_B.sh
#   2) 拿到 A 的公鑰後：把它當參數傳入，送出加密訊息
#        ./run_B.sh <A的公鑰>
#        ./run_B.sh <A的公鑰> "想送的訊息"
#
# Relay IP 可用環境變數覆蓋：RELAY_IP=1.2.3.4 ./run_B.sh <A的公鑰>
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."   # 切到專案根目錄（e2ee.py / p2p_node.py 所在）

# ===== 設定（依需要修改）=====
RELAY_IP="${RELAY_IP:-140.112.30.188}"   # relay server 的 IP（要跟 A 同一台）
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
MESSAGE="${2:-Hi from B over relay}"
if [ -z "$PEER_PUBKEY" ]; then
  echo "ℹ️  還沒收到 A 的公鑰。"
  echo "   把上面的公鑰傳給 A，拿到 A 的公鑰後再執行："
  echo "      ./run_B.sh <A的公鑰> \"想送的訊息\""
  exit 0
fi

echo "📨 送給 A: ${PEER_PUBKEY:0:16}…　訊息：「$MESSAGE」"
echo "🌐 Relay: ${RELAY_IP}:${RELAY_PORT}"
echo

exec python3 -u p2p_node.py --port "$PORT" --name B \
  --server-ip "$RELAY_IP" --server-port "$RELAY_PORT" \
  --peer-pubkey "$PEER_PUBKEY" --message "$MESSAGE"
