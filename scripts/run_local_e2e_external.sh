#!/usr/bin/env bash
# 本机同时启动 receiver + sender（监听 0.0.0.0），便于局域网内其它机器浏览器验证。
# 用法（在仓库根目录）:
#   ./scripts/run_local_e2e_external.sh
# 环境变量可选:
#   RX_PORT=8002 TX_UI=8012 VIDEO_PATH=./data/auto_generated.mp4 TASK_ID=local-e2e-ext

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RX_PORT="${RX_PORT:-8002}"
TX_UI="${TX_UI:-8012}"
TASK_ID="${TASK_ID:-local-e2e-ext}"
VIDEO_PATH="${VIDEO_PATH:-./data/auto_generated.mp4}"
LOG_DIR="${LOG_DIR:-$ROOT/logs/e2e_local}"

mkdir -p "$LOG_DIR"

if [[ ! -f "$VIDEO_PATH" ]]; then
  echo "[error] 视频不存在: $VIDEO_PATH"
  exit 1
fi

if ss -tln 2>/dev/null | grep -q ":${RX_PORT} "; then
  echo "[error] 端口 ${RX_PORT} 已被占用，请改 RX_PORT 或释放端口"
  exit 1
fi
if ss -tln 2>/dev/null | grep -q ":${TX_UI} "; then
  echo "[error] 端口 ${TX_UI} 已被占用，请改 TX_UI 或释放端口"
  exit 1
fi

PRIMARY_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

echo "[1/4] 启动 receiver (0.0.0.0:${RX_PORT}) ..."
python -m app.start --role receiver \
  --host 0.0.0.0 \
  --port "${RX_PORT}" \
  --infer-backend box \
  --report-enabled false \
  --task-id "${TASK_ID}" \
  >"${LOG_DIR}/receiver.log" 2>&1 &
echo $! >"${LOG_DIR}/receiver.pid"

for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${RX_PORT}/metrics" >/dev/null; then
    echo "      receiver 就绪"
    break
  fi
  sleep 0.2
done
if ! curl -sf "http://127.0.0.1:${RX_PORT}/metrics" >/dev/null; then
  echo "[error] receiver 未起来，见 ${LOG_DIR}/receiver.log"
  tail -30 "${LOG_DIR}/receiver.log"
  exit 1
fi

echo "[2/4] 启动 sender UI (0.0.0.0:${TX_UI}) ..."
python -m app.start --role sender \
  --task-id "${TASK_ID}" \
  --video-path "${VIDEO_PATH}" \
  --receiver-url "http://127.0.0.1:${RX_PORT}" \
  --fps 10 \
  --width 640 \
  --height 360 \
  --infer-model-name yolov8 \
  --report-enabled false \
  --ui-host 0.0.0.0 \
  --ui-port "${TX_UI}" \
  >"${LOG_DIR}/sender.log" 2>&1 &
echo $! >"${LOG_DIR}/sender.pid"

for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${TX_UI}/metrics" >/dev/null; then
    echo "      sender 就绪"
    break
  fi
  sleep 0.2
done
if ! curl -sf "http://127.0.0.1:${TX_UI}/metrics" >/dev/null; then
  echo "[error] sender 未起来，见 ${LOG_DIR}/sender.log"
  tail -30 "${LOG_DIR}/sender.log"
  exit 1
fi

echo "[3/4] 触发 start_send ..."
curl -s -X POST "http://127.0.0.1:${TX_UI}/start_send" -F "task_id=${TASK_ID}"
echo

sleep 2
echo "[4/4] receiver /metrics 摘要:"
curl -s "http://127.0.0.1:${RX_PORT}/metrics" | python3 -m json.tool | head -30

echo ""
echo "======== 外部浏览器（将 HOST 换为下面 IP；防火墙需放行 TCP 端口）========"
echo "  本机候选 IP: ${PRIMARY_IP}"
echo "  Receiver:    http://${PRIMARY_IP}:${RX_PORT}/"
echo "  Sender UI:   http://${PRIMARY_IP}:${TX_UI}/"
echo ""
echo "  日志: ${LOG_DIR}/receiver.log  ${LOG_DIR}/sender.log"
echo "  停止: kill \$(cat ${LOG_DIR}/receiver.pid) \$(cat ${LOG_DIR}/sender.pid)"
echo "=================================================================="
