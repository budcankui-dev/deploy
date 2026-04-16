#!/usr/bin/env bash
set -euo pipefail

TASK_ID="${TASK_ID:-task-stack-001}"
REPORT_ENABLED="${REPORT_ENABLED:-false}"

FPS="${FPS:-8}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-360}"

RECEIVER_PORT="${RECEIVER_PORT:-8001}"
SENDER_UI_PORT="${SENDER_UI_PORT:-8012}"
TRAINER_PORT="${TRAINER_PORT:-8013}"

VIDEO_PATH="${VIDEO_PATH:-./test.mp4}"

DB_URL="${DB_URL:-}"
DB_TYPE="${DB_TYPE:-sqlite}"
DB_HOST="${DB_HOST:-10.112.204.7}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-video_infer}"

REDIS_URL="${REDIS_URL:-}"
REDIS_STREAM_KEY="${REDIS_STREAM_KEY:-task_runtime_events}"

TASK_META="${TASK_META:-{\"from\":\"test_stack\"}}"

MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
MINIO_PORT="${MINIO_PORT:-9000}"
MINIO_CONSOLE_PORT="${MINIO_CONSOLE_PORT:-9001}"
MINIO_BUCKET="${MINIO_BUCKET:-datasets}"
MINIO_PREFIX="${MINIO_PREFIX:-tasks}"

REGISTRY="${REGISTRY:-registry.local/video-infer}"
TAG="${TAG:-latest}"

NET_NAME="${NET_NAME:-video_infer_net}"

IMAGE_RECEIVER="${REGISTRY}/video-infer-receiver:${TAG}"
IMAGE_SENDER="${REGISTRY}/video-infer-sender:${TAG}"
IMAGE_TRAINER="${REGISTRY}/model-trainer:${TAG}"

if [[ ! -f "${VIDEO_PATH}" ]]; then
  echo "[error] VIDEO_PATH not found: ${VIDEO_PATH}"
  echo "        你需要准备一个测试视频文件，或把 VIDEO_PATH 指向真实文件。"
  exit 1
fi

echo "[1/7] build images (local)..."
REGISTRY="${REGISTRY}" TAG="${TAG}" ./scripts/build_images.sh

echo "[2/7] prepare docker network: ${NET_NAME}"
docker network inspect "${NET_NAME}" >/dev/null 2>&1 || docker network create "${NET_NAME}" >/dev/null

cleanup() {
  set +e
  docker rm -f minio_node receiver_node sender_node trainer_node >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[3/7] start minio..."
docker rm -f minio_node >/dev/null 2>&1 || true
docker run -d --name minio_node \
  --network "${NET_NAME}" \
  -p "${MINIO_PORT}:${MINIO_PORT}" \
  -p "${MINIO_CONSOLE_PORT}:${MINIO_CONSOLE_PORT}" \
  -e "MINIO_ROOT_USER=${MINIO_ROOT_USER}" \
  -e "MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD}" \
  -v "$(pwd)/minio_data:/data" \
  minio/minio:latest \
  server /data --console-address ":${MINIO_CONSOLE_PORT}" >/dev/null

echo "[4/7] start receiver..."
docker rm -f receiver_node >/dev/null 2>&1 || true
docker run -d --name receiver_node \
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
  --host 0.0.0.0 \
  --port "${RECEIVER_PORT}" >/dev/null

echo "[5/7] start sender (UI enabled)..."
docker rm -f sender_node >/dev/null 2>&1 || true
docker run -d --name sender_node \
  --network "${NET_NAME}" \
  -p "${SENDER_UI_PORT}:${SENDER_UI_PORT}" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/test.mp4:/app/test.mp4" \
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
  --video-path /app/test.mp4 \
  --receiver-url "http://receiver_node:${RECEIVER_PORT}" \
  --fps "${FPS}" \
  --width "${WIDTH}" \
  --height "${HEIGHT}" >/dev/null

echo "[6/7] start trainer..."
docker rm -f trainer_node >/dev/null 2>&1 || true
docker run -d --name trainer_node \
  --network "${NET_NAME}" \
  -p "${TRAINER_PORT}:${TRAINER_PORT}" \
  -v "$(pwd)/data:/app/data" \
  "${IMAGE_TRAINER}" \
  --task-id "${TASK_ID}" \
  --port "${TRAINER_PORT}" \
  --host 0.0.0.0 \
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
  --minio-endpoint "minio_node:${MINIO_PORT}" \
  --access-key "${MINIO_ROOT_USER}" \
  --secret-key "${MINIO_ROOT_PASSWORD}" \
  --bucket "${MINIO_BUCKET}" \
  --prefix "${MINIO_PREFIX}" \
  --work-dir "/app/data/train_runs" \
  --auto-start "false" >/dev/null

echo "[7/7] wait ready..."
for _ in {1..40}; do
  if curl -sf "http://127.0.0.1:${RECEIVER_PORT}/metrics" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

for _ in {1..40}; do
  if curl -sf "http://127.0.0.1:${SENDER_UI_PORT}/metrics" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

for _ in {1..40}; do
  if curl -sf "http://127.0.0.1:${TRAINER_PORT}/metrics" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo
echo "Stack ready:"
echo "  receiver UI: http://127.0.0.1:${RECEIVER_PORT}/"
echo "  sender UI:   http://127.0.0.1:${SENDER_UI_PORT}/"
echo "  trainer UI:  http://127.0.0.1:${TRAINER_PORT}/"
echo
echo "Caution: containers are auto-cleaned on exit. To keep them, remove trap cleanup EXIT."

