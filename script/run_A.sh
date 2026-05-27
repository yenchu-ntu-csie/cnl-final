#!/usr/bin/env bash
# ============================================================
# 電腦 A —— 收訊方 (receiver)
#
# 用法：
#   1) 第一次：直接執行，印出 A 的公鑰，傳給 B
#        ./run_A.sh
#   2) 拿到 B 的公鑰後：把它當參數傳入，正式啟動
#        ./run_A.sh <B的公鑰>
#
# Relay IP 可用環境變數覆蓋：RELAY_IP=1.2.3.4 ./run_A.sh <B的公鑰>
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."   # 切到專案根目錄（e2ee.py / p2p_node.py 所在）

# ===== 設定（依需要修改）=====
RELAY_IP="${RELAY_IP:-140.112.30.183}"   # relay server 的 IP
RELAY_PORT="${RELAY_PORT:-9000}"
PORT=8001
KEYFILE="linkedout_${PORT}.key"

# ===== 顯示自己的公鑰 =====
MY_PUB=$(python3 -c "import e2ee; print(e2ee.public_hex(e2ee.load_or_create_identity('$KEYFILE')))")
echo "============================================================"
echo "🔑 這台 (A) 的公鑰 —— 複製給 B："
echo "   $MY_PUB"
echo "============================================================"

PEER_PUBKEY="${1:-}"
if [ -z "$PEER_PUBKEY" ]; then
  echo "ℹ️  還沒收到 B 的公鑰。"
  echo "   把上面的公鑰傳給 B，拿到 B 的公鑰後再執行："
  echo "      ./run_A.sh <B的公鑰>"
  exit 0
fi

echo "🤝 信任的寄件者 (B): ${PEER_PUBKEY:0:16}…"
echo "🌐 Relay: ${RELAY_IP}:${RELAY_PORT}　等待 B 的加密訊息…"
echo

exec python3 -u p2p_node.py --port "$PORT" --name A \
  --server-ip "$RELAY_IP" --server-port "$RELAY_PORT" \
  --trust "$PEER_PUBKEY"
