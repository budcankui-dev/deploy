#!/usr/bin/env bash
set -euo pipefail

TASK_ID="${TASK_ID:-task-docker-nginx-001}"
RECEIVER_PORT="${RECEIVER_PORT:-8002}"
SENDER_UI_PORT="${SENDER_UI_PORT:-8012}"
NGINX_PORT="${NGINX_PORT:-8088}"
NGINX_SENDER_PORT="${NGINX_SENDER_PORT:-8089}"

RECEIVER_HOST="${RECEIVER_HOST:-::}"
SENDER_UI_HOST="${SENDER_UI_HOST:-::}"
RECEIVER_URL="${RECEIVER_URL:-http://[2025:db8::f49e:16b2:59cb:734]:8002}"

REPORT_ENABLED="${REPORT_ENABLED:-false}"
DB_TYPE="${DB_TYPE:-mysql}"
DB_HOST="${DB_HOST:-10.112.204.7}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-intent}"
REDIS_URL="${REDIS_URL:-redis://:123456@10.112.204.7:6379/0}"
REDIS_STREAM_KEY="${REDIS_STREAM_KEY:-task_runtime_events}"

RECEIVER_IMAGE="${RECEIVER_IMAGE:-video-infer-receiver:local}"
SENDER_IMAGE="${SENDER_IMAGE:-video-infer-sender:local}"
NGINX_IMAGE="${NGINX_IMAGE:-nginx:1.27-alpine}"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NGINX_CONF="${ROOT_DIR}/deploy/nginx/video_infer_dualstack.conf"

if [[ ! -f "${NGINX_CONF}" ]]; then
  echo "[error] nginx config not found: ${NGINX_CONF}"
  exit 1
fi

echo "[1/5] cleanup old containers"
docker rm -f video-infer-receiver video-infer-sender video-infer-nginx >/dev/null 2>&1 || true

echo "[2/5] start receiver container"
docker run -d --name video-infer-receiver --network host \
  -e WEB_DIR=/web \
  "${RECEIVER_IMAGE}" \
  --task-id "${TASK_ID}" \
  --host "${RECEIVER_HOST}" \
  --port "${RECEIVER_PORT}" \
  --infer-backend box \
  --report-enabled "${REPORT_ENABLED}" \
  --db-type "${DB_TYPE}" \
  --db-host "${DB_HOST}" \
  --db-port "${DB_PORT}" \
  --db-user "${DB_USER}" \
  --db-password "${DB_PASSWORD}" \
  --db-name "${DB_NAME}" \
  --redis-url "${REDIS_URL}" \
  --redis-stream-key "${REDIS_STREAM_KEY}" >/dev/null

echo "[3/5] start sender container"
docker run -d --name video-infer-sender --network host \
  -e WEB_DIR=/web \
  "${SENDER_IMAGE}" \
  --task-id "${TASK_ID}" \
  --receiver-url "${RECEIVER_URL}" \
  --ui-host "${SENDER_UI_HOST}" \
  --ui-port "${SENDER_UI_PORT}" \
  --report-enabled "${REPORT_ENABLED}" \
  --db-type "${DB_TYPE}" \
  --db-host "${DB_HOST}" \
  --db-port "${DB_PORT}" \
  --db-user "${DB_USER}" \
  --db-password "${DB_PASSWORD}" \
  --db-name "${DB_NAME}" \
  --redis-url "${REDIS_URL}" \
  --redis-stream-key "${REDIS_STREAM_KEY}" >/dev/null

echo "[4/5] start nginx dual-stack gateway"
docker run -d --name video-infer-nginx --network host \
  -v "${NGINX_CONF}:/etc/nginx/conf.d/default.conf:ro" \
  "${NGINX_IMAGE}" >/dev/null

echo "[5/5] smoke test"
sleep 2
curl -sf "http://127.0.0.1:${NGINX_PORT}/" >/dev/null
curl -sf "http://127.0.0.1:${NGINX_SENDER_PORT}/" >/dev/null

echo "ready:"
echo "  receiver(ui via nginx): http://127.0.0.1:${NGINX_PORT}/"
echo "  sender(ui via nginx):   http://127.0.0.1:${NGINX_SENDER_PORT}/"
echo "  stop all: docker rm -f video-infer-nginx video-infer-sender video-infer-receiver"
