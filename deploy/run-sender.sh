#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 视频推理 · sender 节点启动脚本
#
# 用途：
#   在取流/发送节点上启动 sender 容器。会把本地视频 (VIDEO_PATH) 映射到容器内，
#   并以 FPS/分辨率编排后推给 RECEIVER_URL 做推理。
#
# 用法：
#   # 1. 最小（仅指定 receiver 地址）
#   TASK_ID=demo-001 \
#   RECEIVER_URL='http://[2001:db8::10]:8002' \
#   VIDEO_PATH=/data/videos/test.mp4 \
#       ./deploy/run-sender.sh
#
#   # 2. 完整业务参数
#   TASK_ID=task-42 NODE_NAME=node-sender-01 \
#   RECEIVER_URL='http://10.112.204.20:8002' \
#   VIDEO_PATH=/data/videos/test.mp4 \
#   FPS=10 WIDTH=640 HEIGHT=360 INFER_MODEL_NAME=yolov8 \
#   UI_HOST=:: UI_PORT=8012 \
#   REPORT_ENABLED=true DB_HOST=10.112.204.7 DB_PASSWORD=xxx \
#       ./deploy/run-sender.sh
# -----------------------------------------------------------------------------

set -euo pipefail

# ===== 编排层参数 =====
REGISTRY="${REGISTRY:-10.112.204.7:5000}"
IMAGE_TAG="${IMAGE_TAG:-dev}"
IMAGE="${IMAGE:-${REGISTRY}/video-infer-sender:${IMAGE_TAG}}"

CONTAINER_NAME="${CONTAINER_NAME:-video-infer-sender}"
TASK_ID="${TASK_ID:-task-demo}"
NODE_NAME="${NODE_NAME:-sender}"

# sender 自身的 UI：UI_PORT=0 表示不开 Web UI
UI_HOST="${UI_HOST:-::}"
UI_PORT="${UI_PORT:-8012}"

# 必填：推理节点地址。支持 http://ip:port / http://[v6]:port / 纯 host（自动补 scheme/port）
RECEIVER_URL="${RECEIVER_URL:-http://127.0.0.1:8002}"

# 上报（与 receiver 相同的一组环境变量）
REPORT_ENABLED="${REPORT_ENABLED:-false}"
DB_TYPE="${DB_TYPE:-mysql}"
DB_HOST="${DB_HOST:-10.112.204.7}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-intent}"
DB_URL="${DB_URL:-}"
REDIS_URL="${REDIS_URL:-}"
REDIS_STREAM_KEY="${REDIS_STREAM_KEY:-task_runtime_events}"
TASK_META="${TASK_META:-{}}"

# ===== 业务层参数 =====
VIDEO_PATH="${VIDEO_PATH:-}"           # 宿主机视频文件绝对路径，留空使用镜像内 /app/data/test.mp4
FPS="${FPS:-10}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-360}"
INFER_MODEL_NAME="${INFER_MODEL_NAME:-yolov8}"   # 仅用于上报，展示给前端

# ===== 运行时选项 =====
NETWORK_MODE="${NETWORK_MODE:-host}"
RESTART_POLICY="${RESTART_POLICY:-unless-stopped}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

NET_ARGS=()
if [[ "${NETWORK_MODE}" == "host" ]]; then
  NET_ARGS+=(--network host)
else
  NET_ARGS+=(-p "${UI_PORT}:${UI_PORT}")
fi

VIDEO_ARGS=()
if [[ -n "${VIDEO_PATH}" ]]; then
  if [[ ! -f "${VIDEO_PATH}" ]]; then
    echo "[error] VIDEO_PATH 不是文件: ${VIDEO_PATH}" >&2
    exit 1
  fi
  VIDEO_ARGS+=(-v "${VIDEO_PATH}:/app/data/test.mp4:ro" -e "VIDEO_PATH=/app/data/test.mp4")
fi

APP_DB_ENVS=(
  -e "DB_URL=${DB_URL}"
  -e "DB_TYPE=${DB_TYPE}"
  -e "DB_HOST=${DB_HOST}"
  -e "DB_PORT=${DB_PORT}"
  -e "DB_USER=${DB_USER}"
  -e "DB_PASSWORD=${DB_PASSWORD}"
  -e "DB_NAME=${DB_NAME}"
)

echo "[sender] image=${IMAGE}  name=${CONTAINER_NAME}  ui=${UI_HOST}:${UI_PORT}  receiver=${RECEIVER_URL}"

# shellcheck disable=SC2086
exec docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart "${RESTART_POLICY}" \
  "${NET_ARGS[@]}" \
  -e "TASK_ID=${TASK_ID}" \
  -e "NODE_NAME=${NODE_NAME}" \
  -e "SENDER_UI_HOST=${UI_HOST}" \
  -e "SENDER_UI_PORT=${UI_PORT}" \
  -e "RECEIVER_URL=${RECEIVER_URL}" \
  -e "FPS=${FPS}" \
  -e "WIDTH=${WIDTH}" \
  -e "HEIGHT=${HEIGHT}" \
  -e "INFER_MODEL_NAME=${INFER_MODEL_NAME}" \
  -e "REPORT_ENABLED=${REPORT_ENABLED}" \
  -e "REDIS_URL=${REDIS_URL}" \
  -e "REDIS_STREAM_KEY=${REDIS_STREAM_KEY}" \
  -e "TASK_META=${TASK_META}" \
  "${APP_DB_ENVS[@]}" \
  "${VIDEO_ARGS[@]}" \
  ${EXTRA_ARGS} \
  "${IMAGE}"
