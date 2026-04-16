# 任务上报查询指南（intent）

本指南用于给任务分配系统/运维同事快速接入查询。

目标：

- Redis：保留详细事件（逐帧、细粒度）
- MySQL（`intent`）：保留列表友好的任务状态和聚合指标（仅两张表）

---

## 1. 首次初始化（必须先做）

执行 SQL 脚本，创建数据库 `intent` 及两张表：

```bash
python3 - <<'PY'
import pymysql
from pathlib import Path

host='10.112.204.7'
port=3306
user='root'
password='Bupt@1234'

sql_path=Path('/data/hdd1/zjl/video_infer/sql/mysql_task_schema.sql')
text=sql_path.read_text(encoding='utf-8')
stmts=[s.strip() for s in text.split(';') if s.strip()]

conn=pymysql.connect(host=host, port=port, user=user, password=password, autocommit=True, charset='utf8mb4')
cur=conn.cursor()
for s in stmts:
    cur.execute(s)
cur.execute("USE intent")
cur.execute("SHOW TABLES")
print([r[0] for r in cur.fetchall()])
cur.close(); conn.close()
PY
```

预期仅需这两张业务表：

- `task_runtime_status`
- `task_runtime_metrics`

---

## 2. 启动参数（Redis + MySQL 同时上报）

> 推荐使用拆分参数 `--db-type/--db-host/...`，避免密码中特殊字符导致连接串解析问题。

### receiver

```bash
python -m app.start --role receiver \
  --task-id task-demo-001 \
  --port 8002 \
  --infer-backend box \
  --report-enabled true \
  --db-type mysql \
  --db-host 10.112.204.7 \
  --db-port 3306 \
  --db-user root \
  --db-password 'Bupt@1234' \
  --db-name intent \
  --redis-url 'redis://:123456@10.112.204.7:6379/0' \
  --redis-stream-key 'task_runtime_events'
```

### sender

```bash
python -m app.start --role sender \
  --task-id task-demo-001 \
  --receiver-url http://127.0.0.1:8002 \
  --ui-port 8012 \
  --fps 5 --width 640 --height 360 \
  --report-enabled true \
  --db-type mysql \
  --db-host 10.112.204.7 \
  --db-port 3306 \
  --db-user root \
  --db-password 'Bupt@1234' \
  --db-name intent \
  --redis-url 'redis://:123456@10.112.204.7:6379/0' \
  --redis-stream-key 'task_runtime_events'
```

---

## 3. MySQL 查询（列表展示）

### 3.1 最近任务状态列表

```sql
SELECT
  task_id,
  state,
  started_at,
  ended_at,
  last_update_at
FROM intent.task_runtime_status
ORDER BY last_update_at DESC
LIMIT 100;
```

### 3.2 任务聚合指标（sender/receiver）

```sql
SELECT
  task_id,
  role,
  sample_count,
  infer_avg_ms,
  infer_p95_ms,
  rtt_avg_ms,
  rtt_p95_ms,
  latest_rtt_ms,
  meet_target_count,
  meet_target_ratio,
  target_latency_ms,
  updated_at
FROM intent.task_runtime_metrics
WHERE task_id = 'task-demo-001'
ORDER BY role;
```

### 3.3 状态 + 指标联表（供任务分配系统页面）

```sql
SELECT
  s.task_id,
  s.state,
  s.started_at,
  s.ended_at,
  s.last_update_at,
  m.role,
  m.sample_count,
  m.infer_avg_ms,
  m.infer_p95_ms,
  m.rtt_avg_ms,
  m.rtt_p95_ms,
  m.latest_rtt_ms,
  m.meet_target_count,
  m.meet_target_ratio,
  m.target_latency_ms
FROM intent.task_runtime_status s
LEFT JOIN intent.task_runtime_metrics m
  ON s.task_id = m.task_id
ORDER BY s.last_update_at DESC, m.role ASC
LIMIT 200;
```

---

## 4. Redis 查询（详细事件）

详细事件流在：

- Stream: `task_runtime_events`

示例（Python）：

```python
import redis
r = redis.Redis.from_url("redis://:123456@10.112.204.7:6379/0", decode_responses=True)
entries = r.xrevrange("task_runtime_events", count=200)
for _id, fields in entries:
    if fields.get("task_id") == "task-demo-001":
        print(_id, fields.get("event_type"), fields.get("node_name"), fields.get("latency_ms"))
```

常见事件类型：

- `sender_start` / `receiver_start`
- `frame_inferred`
- `frame_rtt`
- `frame_sent_ack`
- `ui_metrics`

---

## 5. 字段含义建议

- `task_runtime_status`：用于任务列表页（状态、开始/结束/最后更新时间）
- `task_runtime_metrics`：用于任务详情页聚合指标（均值、P95、达标数/达标率）
- Redis Stream：用于排障、回放、细粒度分析（不直接做列表页）

