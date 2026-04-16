#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   RECEIVER_URL='http://[240e:xxxx::30]:8002' \
#   RECEIVER_HOST='::' \
#   RECEIVER_PORT=8002 \
#   DB_HOST='240e:xxxx::10' DB_PORT=3306 DB_USER='root' DB_PASSWORD='***' DB_NAME='intent' \
#   REDIS_URL='redis://:***@[240e:xxxx::20]:6379/0' \
#   scripts/preflight_ipv6_check.sh

RECEIVER_URL="${RECEIVER_URL:-http://127.0.0.1:8002}"
RECEIVER_HOST="${RECEIVER_HOST:-::}"
RECEIVER_PORT="${RECEIVER_PORT:-8002}"

DB_HOST="${DB_HOST:-}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_NAME="${DB_NAME:-intent}"

REDIS_URL="${REDIS_URL:-}"

echo "== [1/6] URL 格式检查 =="
python3 - <<'PY'
import os
from urllib.parse import urlparse

receiver_url = os.getenv("RECEIVER_URL", "").strip()
if not receiver_url:
    raise SystemExit("RECEIVER_URL 为空")

p = urlparse(receiver_url)
if not p.scheme or not p.netloc:
    raise SystemExit(f"RECEIVER_URL 非法: {receiver_url}")

host = p.hostname or ""
port = p.port
print(f"receiver_url={receiver_url}")
print(f"parsed_host={host}, parsed_port={port}")

if ":" in host and "[" not in receiver_url:
    print("WARN: IPv6 建议使用标准格式: http://[IPv6]:PORT")
PY

echo "== [2/6] 监听参数检查 =="
echo "receiver_host=${RECEIVER_HOST}, receiver_port=${RECEIVER_PORT}"
if [[ "${RECEIVER_HOST}" != "::" && "${RECEIVER_HOST}" != "0.0.0.0" && "${RECEIVER_HOST}" != "::0" ]]; then
  echo "WARN: 建议 receiver 使用 --host :: 监听 IPv6"
fi

echo "== [3/6] receiver TCP 可达性检查 =="
python3 - <<'PY'
import os, socket
from urllib.parse import urlparse

u = urlparse(os.getenv("RECEIVER_URL", ""))
host = u.hostname
port = u.port or int(os.getenv("RECEIVER_PORT", "8002"))
if not host:
    raise SystemExit("receiver host 解析失败")

infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
if not infos:
    raise SystemExit("getaddrinfo 无结果")

ok = False
last_err = None
for fam, stype, proto, _, sockaddr in infos:
    s = socket.socket(fam, stype, proto)
    s.settimeout(2.5)
    try:
        s.connect(sockaddr)
        print(f"OK connect -> {sockaddr}")
        ok = True
        break
    except Exception as e:
        last_err = e
    finally:
        s.close()

if not ok:
    raise SystemExit(f"receiver 不可达: {last_err}")
PY

echo "== [4/6] receiver HTTP 健康检查 =="
if curl -g -sS -m 3 "${RECEIVER_URL}/metrics" >/dev/null; then
  echo "OK: ${RECEIVER_URL}/metrics 可访问"
else
  echo "WARN: ${RECEIVER_URL}/metrics 访问失败（若 receiver 未启动可忽略）"
fi

echo "== [5/6] MySQL 可达性检查（可选） =="
if [[ -n "${DB_HOST}" ]]; then
  export DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME
  python3 - <<'PY'
import os
import pymysql
from pymysql.constants import CLIENT

host=os.environ["DB_HOST"]
port=int(os.environ.get("DB_PORT","3306"))
user=os.environ.get("DB_USER","root")
pwd=os.environ.get("DB_PASSWORD","")
db=os.environ.get("DB_NAME","intent")
conn=pymysql.connect(host=host,port=port,user=user,password=pwd,database=db,charset='utf8mb4',client_flag=CLIENT.MULTI_STATEMENTS,connect_timeout=3,read_timeout=3,write_timeout=3)
cur=conn.cursor()
cur.execute("SELECT DATABASE(), NOW()")
print("OK MySQL:", cur.fetchone())
cur.close(); conn.close()
PY
else
  echo "SKIP: 未提供 DB_HOST"
fi

echo "== [6/6] Redis 可达性检查（可选） =="
if [[ -n "${REDIS_URL}" ]]; then
  export REDIS_URL
  python3 - <<'PY'
import os, redis
r=redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
print("OK Redis PING:", r.ping())
PY
else
  echo "SKIP: 未提供 REDIS_URL"
fi

echo "Preflight 检查完成。"

