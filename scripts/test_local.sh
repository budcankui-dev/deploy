#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
PIP_BIN="${PIP_BIN:-pip}"
INSTALL_DEPS="${INSTALL_DEPS:-false}"
TASK_ID="${TASK_ID:-task-local-001}"
REPORT_ENABLED="${REPORT_ENABLED:-false}"
FPS="${FPS:-8}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-360}"
RECEIVER_PORT="${RECEIVER_PORT:-8002}"
DB_URL="${DB_URL:-}"
DB_TYPE="${DB_TYPE:-sqlite}"
DB_HOST="${DB_HOST:-10.112.204.7}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-video_infer}"
REDIS_URL="${REDIS_URL:-}"
REDIS_STREAM_KEY="${REDIS_STREAM_KEY:-task_runtime_events}"
TASK_META="${TASK_META:-{\"from\":\"test_local\"}}"
VIDEO_PATH="${VIDEO_PATH:-./test.mp4}"

mkdir -p data logs

cleanup() {
  set +e
  if [[ -n "${RECEIVER_PID:-}" ]]; then kill "${RECEIVER_PID}" >/dev/null 2>&1 || true; fi
  if [[ -n "${SENDER_PID:-}" ]]; then kill "${SENDER_PID}" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

echo "[1/5] 检查运行环境..."
if [[ "${INSTALL_DEPS}" == "true" ]]; then
  "${PIP_BIN}" install -r requirements.txt >/dev/null
fi

echo "[2/5] 启动 receiver ..."
"${PYTHON_BIN}" -m app.start \
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
  --port "${RECEIVER_PORT}" \
  > logs/receiver.log 2>&1 &
RECEIVER_PID=$!

for _ in {1..20}; do
  if curl -sf "http://127.0.0.1:${RECEIVER_PORT}/metrics" >/dev/null; then
    break
  fi
  sleep 0.5
done

echo "[3/5] 启动 sender ..."
"${PYTHON_BIN}" -m app.start \
  --role sender \
  --task-id "${TASK_ID}" \
  --report-enabled "${REPORT_ENABLED}" \
  --video-path "${VIDEO_PATH}" \
  --receiver-url "http://127.0.0.1:${RECEIVER_PORT}" \
  --fps "${FPS}" \
  --width "${WIDTH}" \
  --height "${HEIGHT}" \
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
  > logs/sender.log 2>&1 &
SENDER_PID=$!

echo "[4/5] 等待 sender 完成..."
wait "${SENDER_PID}"

echo "[5/5] 输出 metrics（可选数据库统计）..."
curl -s "http://127.0.0.1:${RECEIVER_PORT}/metrics" || true
echo

if [[ "${REPORT_ENABLED}" == "true" ]]; then
"${PYTHON_BIN}" - <<'PY'
import sqlite3
from pathlib import Path

db = Path("data/task_runtime.db")
if not db.exists():
    print("未检测到本地 SQLite 文件（如果使用 MySQL 可忽略）：", db)
    raise SystemExit(0)

conn = sqlite3.connect(str(db))
cur = conn.cursor()
print("\n最近10条 task_runtime_events：")
for row in cur.execute("""
SELECT id, task_id, node_name, event_type, frame_id, latency_ms, event_ts
FROM task_runtime_events
ORDER BY id DESC
LIMIT 10
"""):
    print(row)

print("\nframe_inferred 聚合统计：")
for row in cur.execute("""
SELECT COUNT(*), ROUND(AVG(latency_ms),2), ROUND(MAX(latency_ms),2)
FROM task_runtime_events
WHERE event_type='frame_inferred'
"""):
    print("count, avg_ms, max_ms =", row)
conn.close()
PY
fi

echo "测试完成。页面可访问: http://127.0.0.1:${RECEIVER_PORT}/"
