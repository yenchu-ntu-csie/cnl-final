#!/usr/bin/env bash
# ============================================================
# 電腦 A —— 收訊方 (receiver)
#
# 接收方會接受 agents.json（白名單）裡「所有人」傳來的訊息，
# 啟動時不用指定對應誰。
#
# 用法：
#   ./run_A.sh                                  # 直接啟動，接受白名單裡所有人
#   ./run_A.sh <某人的公鑰>                       # 加進白名單（預設 tier=common）再啟動
#   ./run_A.sh <某人的公鑰> <名字>                # 同上，順便給名字
#   ./run_A.sh <某人的公鑰> <名字> <tier>         # 指定 tier：common / task / personal
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
# ${3:+--tier "$3"} 在沒第三個參數時整段消失，有的話就帶 --tier <tier>
if [ -n "${1:-}" ]; then
  python3 agents.py add "$1" --name "${2:-peer}" ${3:+--tier "$3"}
fi

# 準備四個 zone（p2p_node 啟動也會 ensure_share；這裡多做是為了放範例檔）
mkdir -p "share/read-only" "share/read&append" "share/task" "share/personal"
[ -f "share/read-only/notes.md" ] || \
  printf 'LinkedOut 共享筆記（read-only）\n第一行\n' > "share/read-only/notes.md"

# ===== Ollama daemon 檢查（ask op 才用得到，提示用、不擋執行）=====
if curl -s --max-time 1 http://localhost:11434/api/tags >/dev/null 2>&1; then
  MODELS=$(curl -s --max-time 1 http://localhost:11434/api/tags | python3 -c "
import json,sys
try: print(','.join(m['name'] for m in json.load(sys.stdin).get('models',[])) or '(無模型)')
except: print('(讀不到)')
")
  echo "🤖 Ollama daemon OK，可用模型：${MODELS}"
else
  echo "⚠️  Ollama daemon 沒在跑 → 對方下 ask 會回 ai_error"
  echo "   啟動：cd ../ollama && OLLAMA_MODELS=\"\$PWD/models\" ./ollama serve"
fi

# 顯示目前白名單
python3 agents.py list
echo "📁 share/ 四區：read-only、read&append（common）、task（task）、personal（personal）"
echo "🌐 Relay: ${RELAY_IP}:${RELAY_PORT}　等待白名單成員的加密訊息…"
echo

# 啟動：接受 agents.json 白名單裡所有人，不指定對應誰
exec python3 -u p2p_node.py --port "$PORT" --name A \
  --server-ip "$RELAY_IP" --server-port "$RELAY_PORT"
