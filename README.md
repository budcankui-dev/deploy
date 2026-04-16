# 双节点视频上传与推理系统（Docker化）

该项目包含两个节点：

- `sender`：读取视频并按指定帧率上传到推理节点。
- `receiver`：接收视频帧、OpenCV画框、页面展示、统计并上报帧时延。

支持任务分配系统动态注入参数（任务ID、节点IP、端口、帧率、分辨率、数据库地址）。
推理节点默认使用 YOLOv8 识别并画框（可动态切换为固定框模式）。如果 YOLO 推理失败，会自动随机画框兜底。

## 1. 运行方式

### 方式A：本地Python运行

```bash
pip install -r requirements.txt

# 节点2：先启动接收推理节点（MySQL + Redis + 默认YOLO）
python -m app.start --role receiver --task-id task-001 --host 0.0.0.0 --port 8001 \
  --db-type mysql --db-host 10.112.204.7 --db-port 3306 --db-user root --db-password 'Bupt@1234' --db-name video_infer \
  --redis-url 'redis://:123456@10.112.204.7:6379/0' --redis-stream-key task_runtime_events \
  --task-meta '{"job_id":"sched-001","scene":"demo"}' \
  --infer-backend yolo --yolo-model yolov8n.pt --yolo-conf 0.25

# 节点1：再启动上传节点（receiver-url可动态替换成外部分配IP+端口）
python -m app.start --role sender --task-id task-001 --video-path ./test.mp4 --receiver-url http://127.0.0.1:8001 --fps 10 --width 640 --height 360 \
  --db-type mysql --db-host 10.112.204.7 --db-port 3306 --db-user root --db-password 'Bupt@1234' --db-name video_infer \
  --redis-url 'redis://:123456@10.112.204.7:6379/0' --redis-stream-key task_runtime_events \
  --task-meta '{"job_id":"sched-001","scene":"demo"}'
```

页面展示地址：`http://<receiver_ip>:<port>/`

## 2. Docker一键部署

### 构建镜像

```bash
docker build -t video-infer-node:latest .
```

### 启动接收推理节点（节点2）

```bash
docker run -d --name receiver_node -p 8001:8001 \
  -v $(pwd)/data:/app/data \
  video-infer-node:latest \
  --role receiver \
  --task-id task-001 \
  --db-type mysql \
  --db-host 10.112.204.7 \
  --db-port 3306 \
  --db-user root \
  --db-password 'Bupt@1234' \
  --db-name video_infer \
  --redis-url 'redis://:123456@10.112.204.7:6379/0' \
  --redis-stream-key task_runtime_events \
  --task-meta '{"job_id":"sched-001","scene":"demo"}' \
  --host 0.0.0.0 \
  --port 8001
```

### 启动上传节点（节点1）

```bash
docker run -d --name sender_node \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/test.mp4:/app/test.mp4 \
  video-infer-node:latest \
  --role sender \
  --task-id task-001 \
  --db-type mysql \
  --db-host 10.112.204.7 \
  --db-port 3306 \
  --db-user root \
  --db-password 'Bupt@1234' \
  --db-name video_infer \
  --redis-url 'redis://:123456@10.112.204.7:6379/0' \
  --redis-stream-key task_runtime_events \
  --task-meta '{"job_id":"sched-001","scene":"demo"}' \
  --video-path /app/test.mp4 \
  --receiver-url http://<receiver_ip>:8001 \
  --fps 10 \
  --width 640 \
  --height 360
```

> 任务分配系统只需在启动命令中替换参数即可，无需提前写死对端IP/端口。

## 3. 数据库上报

程序会写入 `task_runtime_events` 表，记录：

- 开始/结束时间（`sender_start/sender_stop`、`receiver_start/receiver_stop`）
- 每帧推理时延（`frame_inferred`）
- 发送成功回执时延（`frame_sent_ack`）
- 异常信息（`sender_error`、`frame_send_error`）

默认支持 MySQL（推荐）与 SQLite。

- 直接给连接串：`--db-url "mysql+pymysql://root:密码@10.112.204.7:3306/video_infer?charset=utf8mb4"`
- 或拆分参数：`--db-type mysql --db-host ... --db-port ... --db-user ... --db-password ... --db-name ...`
- 可选 Redis 实时上报：`--redis-url "redis://:123456@10.112.204.7:6379/0" --redis-stream-key task_runtime_events`
- 可选任务扩展元数据：`--task-meta '{"job_id":"xxx","biz":"xxx"}'`
- 推理后端参数（receiver）：`--infer-backend yolo|box --yolo-model yolov8n.pt --yolo-conf 0.25`

多节点任务通用建表脚本见：`sql/mysql_task_schema.sql`

## 4. HTTP接口

- `POST /infer_frame`：接收帧并推理
- `GET /metrics?task_id=<uuid>`：返回指定 task_id 的 receiver 统计（不混合）
- `GET /stream.mjpg`：视频流
- `GET /`：页面展示

Sender 可选看板参数（默认关闭）：

- `--ui-port 0`：关闭 sender 前端
- `--ui-port 8012 --ui-host 0.0.0.0`：开启 sender 前端
- 开启后可访问：`http://<sender_ip>:8012/`
- sender 前端支持上传视频并开始发送（也可不上传，继续使用默认 `test.mp4`）

## 5. 一键测试脚本

### 本地 Python 进程测试

```bash
./scripts/test_local.sh
```

可选参数（示例）：

```bash
TASK_ID=task-local-002 FPS=12 WIDTH=1280 HEIGHT=720 RECEIVER_PORT=8002 ./scripts/test_local.sh
```

### Docker 测试

```bash
./scripts/test_docker.sh
```

可选参数（示例）：

```bash
TASK_ID=task-docker-002 FPS=10 WIDTH=960 HEIGHT=540 RECEIVER_PORT=8003 IMAGE=video-infer-node:latest ./scripts/test_docker.sh
```

### 测试结果检查

- 页面：`http://127.0.0.1:<RECEIVER_PORT>/`
- 指标：`curl http://127.0.0.1:<RECEIVER_PORT>/metrics`
- 数据库：`data/task_runtime.db`（脚本会输出最近事件与时延聚合）
