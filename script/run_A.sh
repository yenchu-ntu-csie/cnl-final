#!/usr/bin/env bash
# ============================================================
# 電腦 A —— 收訊方 (receiver)
#
# 接收方會接受 agents.json（白名單）裡「所有人」傳來的訊息，
# 啟動時不用指定對應誰。
#
# 用法：
#   ./run_A.sh                 # 直接啟動，接受白名單裡所有人
#   ./run_A.sh <某人的公鑰>     # 先把這把公鑰加進白名單（持久化），再啟動
#
# Relay IP 可用環境變數覆蓋：RELAY_IP=1.2.3.4 ./run_A.sh
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
echo "🔑 這台 (A) 的公鑰 —— 複製給對方："
echo "   $MY_PUB"
echo "============================================================"

# 若有帶參數，先把對方公鑰加進白名單（之後就記住了，不用每次帶）
if [ -n "${1:-}" ]; then
  python3 agents.py add "$1" --name "${2:-peer}"
fi

# 準備分享資料夾：share/read-only（對方只能讀）、share/read&append（對方可讀可追加）
mkdir -p "share/read-only" "share/read&append"
[ -f "share/read-only/notes.md" ] || printf 'LinkedOut 共享筆記（read-only）\n第一行\n' > "share/read-only/notes.md"

# 顯示目前白名單
python3 agents.py list
echo "📁 分享資料夾 share/：read-only/（唯讀）、read&append/（可追加）"
echo "🌐 Relay: ${RELAY_IP}:${RELAY_PORT}　等待白名單成員的加密訊息…"
echo

# 啟動：接受 agents.json 白名單裡所有人，不指定對應誰
exec python3 -u p2p_node.py --port "$PORT" --name A \
  --server-ip "$RELAY_IP" --server-port "$RELAY_PORT"
