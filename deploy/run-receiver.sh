#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 视频推理 · receiver 节点启动脚本
#
# 用途：
#   在推理节点（GPU/CPU 均可，CPU 仅建议 box 模式）上启动 receiver 容器。
#   所有参数都支持通过环境变量下发，方便任务分配系统直接 envsubst 后执行。
#
# 用法：
#   # 1. 最小 (box 推理，无 GPU 依赖)
#   TASK_ID=demo-001 ./deploy/run-receiver.sh
#
#   # 2. YOLO 推理 (需 nvidia-container-runtime + 兼容 GPU)
#   TASK_ID=demo-001 INFER_BACKEND=yolo YOLO_MODEL=yolov8 USE_GPU=true \
#       ./deploy/run-receiver.sh
#
#   # 3. 指定完整参数（由调度下发的真实参数）
#   TASK_ID=task-42 \
#   NODE_NAME=node-receiver-01 \
#   PORT=8002 \
#   INFER_BACKEND=yolo YOLO_MODEL=yolov8 YOLO_CONF=0.25 \
#   DB_HOST=10.112.204.7 DB_PORT=3306 DB_USER=root DB_PASSWORD=xxx DB_NAME=intent \
#   REDIS_URL=redis://10.112.204.7:6379/0 \
#   REPORT_ENABLED=true \
#       ./deploy/run-receiver.sh
# -----------------------------------------------------------------------------

set -euo pipefail

# ===== 编排层参数（调度系统下发，生产默认只读）=====
REGISTRY="${REGISTRY:-10.112.204.7:5000}"
IMAGE_TAG="${IMAGE_TAG:-dev}"
IMAGE="${IMAGE:-${REGISTRY}/video-infer-receiver:${IMAGE_TAG}}"

CONTAINER_NAME="${CONTAINER_NAME:-video-infer-receiver}"
TASK_ID="${TASK_ID:-task-demo}"
NODE_NAME="${NODE_NAME:-receiver}"

# 监听地址：容器内部监听 :: 做双栈；宿主通过 --network host 直接暴露
HOST="${HOST:-::}"
PORT="${PORT:-8002}"

# 上报（数据库 / Redis）
REPORT_ENABLED="${REPORT_ENABLED:-false}"
DB_TYPE="${DB_TYPE:-mysql}"
DB_HOST="${DB_HOST:-10.112.204.7}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-intent}"
DB_URL="${DB_URL:-}"                   # 若直接给完整 URL 会覆盖上面 5 项
REDIS_URL="${REDIS_URL:-}"
REDIS_STREAM_KEY="${REDIS_STREAM_KEY:-task_runtime_events}"
TASK_META="${TASK_META:-{}}"

# ===== 业务层参数 =====
INFER_BACKEND="${INFER_BACKEND:-box}"  # yolo | box
YOLO_MODEL="${YOLO_MODEL:-yolov8}"     # 已内置 alias -> 权重文件名映射
YOLO_CONF="${YOLO_CONF:-0.25}"

# ===== 运行时选项 =====
USE_GPU="${USE_GPU:-false}"            # yolo 模式建议 true
NETWORK_MODE="${NETWORK_MODE:-host}"   # host 可直通 IPv6 与任意端口
RESTART_POLICY="${RESTART_POLICY:-unless-stopped}"
EXTRA_ARGS="${EXTRA_ARGS:-}"           # 透传到 docker run（例如挂载权重目录）

# -----------------------------------------------------------------------------
# 组装命令
# -----------------------------------------------------------------------------
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

GPU_ARGS=()
if [[ "${USE_GPU}" == "true" ]]; then
  GPU_ARGS+=(--gpus all)
fi

NET_ARGS=()
if [[ "${NETWORK_MODE}" == "host" ]]; then
  NET_ARGS+=(--network host)
else
  NET_ARGS+=(-p "${PORT}:${PORT}")
fi

# 允许只用 DB_URL 或分字段两种方式
APP_DB_ENVS=(
  -e "DB_URL=${DB_URL}"
  -e "DB_TYPE=${DB_TYPE}"
  -e "DB_HOST=${DB_HOST}"
  -e "DB_PORT=${DB_PORT}"
  -e "DB_USER=${DB_USER}"
  -e "DB_PASSWORD=${DB_PASSWORD}"
  -e "DB_NAME=${DB_NAME}"
)

echo "[receiver] image=${IMAGE}  name=${CONTAINER_NAME}  host=${HOST}:${PORT}  backend=${INFER_BACKEND}  gpu=${USE_GPU}"

# shellcheck disable=SC2086
exec docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart "${RESTART_POLICY}" \
  "${NET_ARGS[@]}" \
  "${GPU_ARGS[@]}" \
  -e "TASK_ID=${TASK_ID}" \
  -e "NODE_NAME=${NODE_NAME}" \
  -e "HOST=${HOST}" \
  -e "PORT=${PORT}" \
  -e "INFER_BACKEND=${INFER_BACKEND}" \
  -e "YOLO_MODEL=${YOLO_MODEL}" \
  -e "YOLO_CONF=${YOLO_CONF}" \
  -e "REPORT_ENABLED=${REPORT_ENABLED}" \
  -e "REDIS_URL=${REDIS_URL}" \
  -e "REDIS_STREAM_KEY=${REDIS_STREAM_KEY}" \
  -e "TASK_META=${TASK_META}" \
  "${APP_DB_ENVS[@]}" \
  ${EXTRA_ARGS} \
  "${IMAGE}"
