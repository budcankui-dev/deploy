#!/usr/bin/env bash
set -euo pipefail

# 只拉起视频推理相关的两个节点：receiver + sender（不启动 minio/trainer）

TASK_ID="${TASK_ID:-task-video-001}"
REPORT_ENABLED="${REPORT_ENABLED:-false}"

FPS="${FPS:-8}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-360}"

RECEIVER_PORT="${RECEIVER_PORT:-8002}"
SENDER_UI_PORT="${SENDER_UI_PORT:-8014}"

VIDEO_PATH="${VIDEO_PATH:-./test.mp4}"
AUTO_GENERATE_VIDEO="${AUTO_GENERATE_VIDEO:-false}"
if [[ "${VIDEO_PATH}" == /* ]]; then
  VIDEO_ABS="${VIDEO_PATH}"
else
  VIDEO_ABS="$(pwd)/${VIDEO_PATH#./}"
fi

DB_URL="${DB_URL:-}"
DB_TYPE="${DB_TYPE:-sqlite}"
DB_HOST="${DB_HOST:-10.112.204.7}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-video_infer}"

REDIS_URL="${REDIS_URL:-}"
REDIS_STREAM_KEY="${REDIS_STREAM_KEY:-task_runtime_events}"
TASK_META="${TASK_META:-{\"from\":\"test_video_infer\"}}"

REGISTRY="${REGISTRY:-registry.local/video-infer}"
TAG="${TAG:-latest}"
SKIP_BUILD="${SKIP_BUILD:-false}"
USE_BUNDLED_VIDEO="${USE_BUNDLED_VIDEO:-false}"
RECEIVER_GPUS="${RECEIVER_GPUS:-false}"
INFER_BACKEND="${INFER_BACKEND:-yolo}"

NET_NAME="${NET_NAME:-video_infer_net}"

MINIO_REQUIRED="${MINIO_REQUIRED:-false}"
if [[ "${MINIO_REQUIRED}" == "true" ]]; then
  echo "[warning] 该脚本默认不启动 minio；MINIO_REQUIRED=true 但当前仍不支持 minio"
fi

IMAGE_RECEIVER="${REGISTRY}/video-infer-receiver:${TAG}"
IMAGE_SENDER="${REGISTRY}/video-infer-sender:${TAG}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[error] docker 未安装或不可用。请在有 docker 的环境执行该脚本。"
  exit 1
fi

mkdir -p data logs

SENDER_VIDEO_PATH="/app/test.mp4"
SENDER_VIDEO_VOL_ARGS=(-v "${VIDEO_ABS}:/app/test.mp4")

if [[ ! -f "${VIDEO_PATH}" ]]; then
  if [[ "${USE_BUNDLED_VIDEO}" == "true" ]]; then
    echo "[info] VIDEO_PATH not on host (${VIDEO_PATH})，使用镜像内 /app/data/test.mp4（构建 sender 时已 COPY）"
    SENDER_VIDEO_PATH="/app/data/test.mp4"
    SENDER_VIDEO_VOL_ARGS=()
  elif [[ "${AUTO_GENERATE_VIDEO}" == "true" ]]; then
    echo "[info] VIDEO_PATH not found: ${VIDEO_PATH}"
    echo "[info] AUTO_GENERATE_VIDEO=true，开始生成联调用测试视频..."
    GEN_PATH="./data/auto_generated.mp4"
    echo "[info] generate to: ${GEN_PATH}"
    VIDEO_PATH="${GEN_PATH}" python3 - <<'PY'
import os, sys
path = os.environ.get("VIDEO_PATH", "./auto_generated.mp4")
try:
    import cv2
    import numpy as np
except Exception as e:
    print("[error] 无法导入依赖，不能自动生成视频：", e)
    sys.exit(1)

os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
fourcc = cv2.VideoWriter_fourcc(*"mp4v")
writer = cv2.VideoWriter(path, fourcc, 10.0, (640, 360))
if not writer.isOpened():
    raise RuntimeError("VideoWriter open failed")
for i in range(120):
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    x = int((i * 4) % 560)
    y = int((i * 2) % 280)
    cv2.rectangle(img, (x, y), (x + 80, y + 80), (0, 255, 0), 2)
    cv2.putText(img, f"auto-test frame={i}", (80, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    writer.write(img)
writer.release()
print("[info] generated:", path)
PY
    VIDEO_PATH="${GEN_PATH}"
    if [[ "${VIDEO_PATH}" == /* ]]; then
      VIDEO_ABS="${VIDEO_PATH}"
    else
      VIDEO_ABS="$(pwd)/${VIDEO_PATH#./}"
    fi
    SENDER_VIDEO_PATH="/app/test.mp4"
    SENDER_VIDEO_VOL_ARGS=(-v "${VIDEO_ABS}:/app/test.mp4")
  else
    echo "[error] VIDEO_PATH not found: ${VIDEO_PATH}"
    echo "[error] 请提供已有视频文件；默认使用仓库根目录的 test.mp4。"
    echo "[error] 如需自动生成联调视频，可设置 AUTO_GENERATE_VIDEO=true。"
    echo "[error] 或使用已构建的 sender 镜像内测试视频：USE_BUNDLED_VIDEO=true"
    exit 1
  fi
fi

echo "[1/5] build receiver/sender images (local)..."
INSTALL_YOLO="${INSTALL_YOLO:-false}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-}"

if [[ "${SKIP_BUILD}" == "true" ]]; then
  echo "[1/5] SKIP_BUILD=true，跳过构建，直接使用: ${IMAGE_RECEIVER} / ${IMAGE_SENDER}"
else
BUILD_ARGS=(--build-arg INSTALL_YOLO="${INSTALL_YOLO}")
if [[ -n "${PIP_INDEX_URL}" ]]; then
  BUILD_ARGS+=(--build-arg PIP_INDEX_URL="${PIP_INDEX_URL}")
fi
if [[ -n "${PIP_TRUSTED_HOST}" ]]; then
  BUILD_ARGS+=(--build-arg PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}")
fi

docker build -f deploy/docker/Dockerfile.video-infer-receiver "${BUILD_ARGS[@]}" -t "${IMAGE_RECEIVER}" .
docker build -f deploy/docker/Dockerfile.video-infer-sender "${BUILD_ARGS[@]}" -t "${IMAGE_SENDER}" .
fi

RECEIVER_GPU_ARGS=()
if [[ "${RECEIVER_GPUS}" == "true" ]]; then
  RECEIVER_GPU_ARGS=(--gpus all)
fi

echo "[2/5] prepare docker network: ${NET_NAME}"
docker network inspect "${NET_NAME}" >/dev/null 2>&1 || docker network create "${NET_NAME}" >/dev/null

cleanup() {
  set +e
  docker rm -f receiver_node sender_node >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[3/5] start receiver..."
docker run -d --name receiver_node \
  "${RECEIVER_GPU_ARGS[@]}" \
  --network "${NET_NAME}" \
  -p "${RECEIVER_PORT}:${RECEIVER_PORT}" \
  -v "$(pwd)/data:/app/data" \
  "${IMAGE_RECEIVER}" \
  --task-id "${TASK_ID}" \
  --report-enabled "${REPORT_ENABLED}" \
  --db-url "${DB_URL}" \
  --db-type "${DB_TYPE}" \
  --db-host "${DB_HOST}" \
  --db-port "${DB_PORT}" \
  --db-user "${DB_USER}" \
  --db-password "${DB_PASSWORD}" \
  --db-name "${DB_NAME}" \
  --redis-url "${REDIS_URL}" \
  --redis-stream-key "${REDIS_STREAM_KEY}" \
  --task-meta "${TASK_META}" \
  --infer-backend "${INFER_BACKEND}" \
  --host 0.0.0.0 \
  --port "${RECEIVER_PORT}" >/dev/null

echo "[4/5] start sender (UI enabled)..."
docker run -d --name sender_node \
  --network "${NET_NAME}" \
  -p "${SENDER_UI_PORT}:${SENDER_UI_PORT}" \
  -v "$(pwd)/data:/app/data" \
  "${SENDER_VIDEO_VOL_ARGS[@]}" \
  "${IMAGE_SENDER}" \
  --task-id "${TASK_ID}" \
  --ui-host 0.0.0.0 \
  --ui-port "${SENDER_UI_PORT}" \
  --report-enabled "${REPORT_ENABLED}" \
  --db-url "${DB_URL}" \
  --db-type "${DB_TYPE}" \
  --db-host "${DB_HOST}" \
  --db-port "${DB_PORT}" \
  --db-user "${DB_USER}" \
  --db-password "${DB_PASSWORD}" \
  --db-name "${DB_NAME}" \
  --redis-url "${REDIS_URL}" \
  --redis-stream-key "${REDIS_STREAM_KEY}" \
  --task-meta "${TASK_META}" \
  --video-path "${SENDER_VIDEO_PATH}" \
  --receiver-url "http://receiver_node:${RECEIVER_PORT}" \
  --fps "${FPS}" \
  --width "${WIDTH}" \
  --height "${HEIGHT}" >/dev/null

echo "[5/5] trigger send + wait metrics..."
for _ in {1..40}; do
  if curl -sf "http://127.0.0.1:${RECEIVER_PORT}/metrics" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

curl -s -X POST "http://127.0.0.1:${SENDER_UI_PORT}/start_send" \
  -H "Content-Type: multipart/form-data" \
  -F "task_id=${TASK_ID}" >/dev/null 2>&1 || true

sleep 3
echo
echo "Stack ready:"
echo "  receiver UI: http://127.0.0.1:${RECEIVER_PORT}/"
echo "  sender UI:   http://127.0.0.1:${SENDER_UI_PORT}/"
echo
echo "receiver /metrics (latest):"
curl -s "http://127.0.0.1:${RECEIVER_PORT}/metrics?task_id=${TASK_ID}" || true

