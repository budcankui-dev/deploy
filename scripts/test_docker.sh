#!/usr/bin/env bash
set -euo pipefail

TASK_ID="${TASK_ID:-task-docker-001}"
REPORT_ENABLED="${REPORT_ENABLED:-false}"
FPS="${FPS:-8}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-360}"
RECEIVER_PORT="${RECEIVER_PORT:-8002}"
IMAGE="${IMAGE:-video-infer-node:latest}"
DB_URL="${DB_URL:-}"
DB_TYPE="${DB_TYPE:-sqlite}"
DB_HOST="${DB_HOST:-10.112.204.7}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-video_infer}"
REDIS_URL="${REDIS_URL:-}"
REDIS_STREAM_KEY="${REDIS_STREAM_KEY:-task_runtime_events}"
TASK_META="${TASK_META:-{\"from\":\"test_docker\"}}"

mkdir -p data logs

echo "[1/6] 构建镜像 ${IMAGE} ..."
docker build -t "${IMAGE}" . >/dev/null

echo "[2/6] 清理旧容器..."
docker rm -f receiver_node sender_node >/dev/null 2>&1 || true

echo "[3/6] 启动 receiver 容器..."
docker run -d --name receiver_node -p "${RECEIVER_PORT}:${RECEIVER_PORT}" \
  -v "$(pwd)/data:/app/data" \
  "${IMAGE}" \
  --role receiver \
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

for _ in {1..30}; do
  if curl -sf "http://127.0.0.1:${RECEIVER_PORT}/metrics" >/dev/null; then
    break
  fi
  sleep 0.5
done

echo "[4/6] 启动 sender 容器..."
docker run -d --name sender_node \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/test.mp4:/app/test.mp4" \
  "${IMAGE}" \
  --role sender \
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
  --video-path /app/test.mp4 \
  --receiver-url "http://host.docker.internal:${RECEIVER_PORT}" \
  --fps "${FPS}" \
  --width "${WIDTH}" \
  --height "${HEIGHT}" >/dev/null

echo "[5/6] 等待 sender 结束..."
while true; do
  status="$(docker inspect -f '{{.State.Status}}' sender_node)"
  [[ "${status}" == "exited" ]] && break
  sleep 1
done

echo "[6/6] 输出 metrics 与关键日志..."
curl -s "http://127.0.0.1:${RECEIVER_PORT}/metrics" || true
echo
echo "--- receiver logs(最后30行) ---"
docker logs receiver_node 2>&1 | tail -n 30
echo "--- sender logs(最后30行) ---"
docker logs sender_node 2>&1 | tail -n 30

echo "测试完成。页面可访问: http://127.0.0.1:${RECEIVER_PORT}/"
echo "清理命令: docker rm -f receiver_node sender_node"
