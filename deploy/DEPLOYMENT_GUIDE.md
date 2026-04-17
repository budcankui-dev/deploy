# 视频推理双节点任务 · 部署与联调指南

> 适用场景：由“任务分配系统”在两台节点上分别拉起 `sender` 与 `receiver` 容器，
> 完成一路视频的推流 → 推理 → 展示链路。本文档覆盖从调度下发到节点联调的全流程，
> 供运维 / 交付 / 研发三方对齐。

---

## 1. 架构总览

```
┌────────────────────────────┐      HTTP POST /ingest_frame        ┌────────────────────────────┐
│   节点 A: SENDER            │ ──────────────────────────────────▶ │   节点 B: RECEIVER          │
│   video-infer-sender:dev    │     (JPEG bytes, RTT 统计)          │   video-infer-receiver:dev  │
│                            │ ◀────────────────────────────────── │                            │
│   - 读本地视频 / 前端上传   │         JSON 推理结果                │   - 解码 + 推理 (yolo/box) │
│   - 按 FPS/WH 重编码推送    │                                     │   - 画框 + MJPEG 视频流    │
│   - 提供 /metrics + UI      │                                     │   - 提供 /metrics + UI     │
└────────────────────────────┘                                     └────────────────────────────┘
               │                                                                    │
               └──────────── 可选：MySQL 事件表 / Redis Stream 上报 ────────────────┘
```

- 两节点可以同机房同网段，也可以 IPv4 / IPv6 混合（容器内已做双栈监听）。
- 数据库 / Redis 可选，冒烟联调时用 `REPORT_ENABLED=false` 跳过。
- 镜像统一托管在私有仓库：`10.112.204.7:5000`。

---

## 2. 任务分配系统下发的字段契约

调度把一个任务拆成两个子任务（`sender_task`、`receiver_task`），每个子任务被分配到一个具体节点。分配结果最少包含下面这些字段：

### 2.1 通用字段（两侧都要）

| 字段 | 说明 | 示例 |
| --- | --- | --- |
| `task_id` | 全局任务号，两侧保持一致 | `task-2026-0417-001` |
| `node_name` | 当前节点业务别名，用于上报落库 | `node-a-sender` |
| `db_*` 或 `DB_URL` | MySQL 连接信息（二选一） | `mysql+pymysql://root:***@10.112.204.7:3306/intent` |
| `redis_url` | Redis 连接，空串=不用 | `redis://10.112.204.7:6379/0` |
| `report_enabled` | 是否向 DB/Redis 上报 | `true` / `false` |
| `task_meta` | 任意 JSON，透传给运行期 | `{"campus":"BJ-01"}` |

### 2.2 receiver 专有字段

| 分类 | 字段 | 说明 | 默认 |
| --- | --- | --- | --- |
| 编排层 | `host` | 监听地址，推荐 `::` 开双栈 | `::` |
| 编排层 | `port` | 监听端口 | `8002` |
| 业务层 | `infer_backend` | `yolo` 或 `box`（无 GPU 用 box） | `box` |
| 业务层 | `yolo_model` | 模型别名，已做 alias → `.pt` 映射 | `yolov8` |
| 业务层 | `yolo_conf` | 推理置信度阈值 | `0.25` |

### 2.3 sender 专有字段

| 分类 | 字段 | 说明 | 默认 |
| --- | --- | --- | --- |
| 编排层 | `ui_host` / `ui_port` | 本机 UI 监听，`0` 关闭 | `::` / `0` |
| 编排层 | `receiver_url` | 调度必须写入 receiver 节点的 IP/端口，支持 `http://[v6]:port` | — |
| 业务层 | `video_path` | 取流源；本文档示例用本地 mp4 | `./data/test.mp4` |
| 业务层 | `fps` / `width` / `height` | 目标编排规格 | `10` / `640` / `360` |
| 业务层 | `infer_model_name` | 仅用于展示/上报 | `yolov8` |

> 以上字段最终都会被映射为 `docker run -e KEY=VAL …`，对应脚本
> `deploy/run-sender.sh` / `deploy/run-receiver.sh` 已经把映射固化好。

### 2.4 参考分配示例

```yaml
task_id: task-2026-0417-001
report_enabled: true
db_url: "mysql+pymysql://root:xxx@10.112.204.7:3306/intent?charset=utf8mb4"
redis_url: "redis://10.112.204.7:6379/0"

receiver:
  node: node-b
  ip:   10.112.204.20
  host: "::"
  port: 8002
  infer_backend: yolo
  yolo_model: yolov8
  yolo_conf: 0.25

sender:
  node: node-a
  ip:   10.112.204.19
  receiver_url: "http://10.112.204.20:8002"
  ui_host: "::"
  ui_port: 8012
  video_path: /data/videos/test.mp4
  fps: 10
  width: 640
  height: 360
  infer_model_name: yolov8
```

---

## 3. 节点环境前置

两台节点都需要满足：

1. Docker ≥ 20.10；`yolo` 模式的 receiver 还需要 `nvidia-container-toolkit`。
2. 私有仓库白名单（一次性）：

   ```bash
   sudo REGISTRY=10.112.204.7:5000 ./deploy/setup-docker-daemon.sh
   ```

   脚本会追加 `insecure-registries` 并重启 docker，最后用 `/v2/_catalog` 自检。
3. 网络：
   - sender 节点能 TCP 直连 `receiver.ip:port`（`nc -zv <ip> 8002` 可通）。
   - IPv4/IPv6 混合时确认 `sysctl net.ipv6.bindv6only=0`（默认即 0）。
4. 防火墙：放行两端端口（默认 `8002` / `8012`）。
5. 镜像已推送到 `10.112.204.7:5000/video-infer-sender:dev`、
   `10.112.204.7:5000/video-infer-receiver:dev`。升级时在节点 `docker pull` 即可。

---

## 4. 分节点启动步骤

### 4.1 节点 B · 启动 receiver

```bash
docker pull 10.112.204.7:5000/video-infer-receiver:dev

# box 后端（无 GPU 依赖），冒烟用
TASK_ID=task-2026-0417-001 \
NODE_NAME=node-b-receiver \
PORT=8002 \
INFER_BACKEND=box \
REPORT_ENABLED=false \
./deploy/run-receiver.sh

# 或：yolo + GPU（需要 yolo 版镜像）
TASK_ID=task-2026-0417-001 \
INFER_BACKEND=yolo YOLO_MODEL=yolov8 USE_GPU=true \
IMAGE=10.112.204.7:5000/video-infer-receiver:yolo \
REPORT_ENABLED=true DB_HOST=10.112.204.7 DB_PASSWORD='xxx' \
REDIS_URL=redis://10.112.204.7:6379/0 \
./deploy/run-receiver.sh
```

就绪校验：

```bash
curl -sf http://127.0.0.1:8002/metrics | head
curl -sf http://[::1]:8002/metrics      | head        # 双栈确认
```

浏览器打开 `http://<receiver-ip>:8002/`，能看到空的推理画面即可。

### 4.2 节点 A · 启动 sender

```bash
docker pull 10.112.204.7:5000/video-infer-sender:dev

TASK_ID=task-2026-0417-001 \
NODE_NAME=node-a-sender \
RECEIVER_URL='http://10.112.204.20:8002' \
VIDEO_PATH=/data/videos/test.mp4 \
UI_HOST=:: UI_PORT=8012 \
FPS=10 WIDTH=640 HEIGHT=360 INFER_MODEL_NAME=yolov8 \
REPORT_ENABLED=false \
./deploy/run-sender.sh
```

就绪校验：

```bash
curl -sf http://127.0.0.1:8012/metrics
```

浏览器打开 `http://<sender-ip>:8012/`：
- `receiver_url` 字段只读填充为调度下发值。
- 点击“开始发送”或执行
  `curl -X POST http://127.0.0.1:8012/start_send -F task_id=task-2026-0417-001`。

---

## 5. 端到端验证清单

sender 触发 `/start_send` 后 2~5 秒内：

| 位置 | 指标 | 期望 |
| --- | --- | --- |
| sender `/metrics` | `sent_frames` | 持续递增 |
| sender `/metrics` | `rtt_ms_avg` | 有数字，不为 `null` |
| receiver `/metrics` | `received_frames` | 与 sender 基本对齐 |
| receiver `/metrics` | `infer_latency_ms_avg` | 有数字 |
| receiver `/video` 页 | MJPEG 画面 | 能看到推理框 |
| MySQL `task_runtime_events` | sender/receiver 两条启动事件（仅 `REPORT_ENABLED=true`） | 有 |

一键汇总：

```bash
echo "-- sender --"   && curl -s http://127.0.0.1:8012/metrics | python3 -m json.tool | head -20
echo "-- receiver --" && curl -s http://<receiver-ip>:8002/metrics | python3 -m json.tool | head -20
```

---

## 6. 常见问题速查

| 现象 | 定位 | 处理 |
| --- | --- | --- |
| 浏览器访问 `http://ip:port/` 被拒 | 宿主执行 `ss -tlnp \| grep PORT` | 如果只监听 `:::PORT` 无 `0.0.0.0`，确认 `HOST=::` 已传入；代码里 `runtime/net.py` 已强制双栈 |
| sender 日志报 `Connection refused` | receiver 未就绪或端口不通 | 先在 sender 机器 `curl http://<receiver-ip>:8002/metrics`；防火墙 / 安全组最常见 |
| receiver `yolo` 模式启动即挂，报 `numpy._ARRAY_API not found` | `numpy` ABI 冲突 | 使用 `:dev` 的 yolo 版镜像（Dockerfile 已钉 `numpy==1.24.4`） |
| yolo 模式不吃 GPU | P40 为 Pascal SM 6.1 | 需 CUDA 12.1 运行时镜像；CUDA 13 不再支持 Pascal |
| `receiver_url` 写成 `http://::1:8002` 报错 | IPv6 未加方括号 | 代码里已做 `_normalize_receiver_url`；手填请写 `http://[::1]:8002` |
| 想禁用上报做纯冒烟 | —— | `REPORT_ENABLED=false`，跳过 DB/Redis 连接 |

---

## 7. 本仓库相关脚本一览

| 路径 | 作用 |
| --- | --- |
| `deploy/run-sender.sh` | 在任意节点启动 sender 容器，参数全部走环境变量 |
| `deploy/run-receiver.sh` | 同上，启动 receiver；支持 box / yolo + GPU |
| `deploy/setup-docker-daemon.sh` | 一次性把私有仓库加入 insecure-registries |
| `deploy/docker/Dockerfile.video-infer-sender` | 构建 sender 镜像 |
| `deploy/docker/Dockerfile.video-infer-receiver` | 构建 receiver 镜像（含 `INSTALL_YOLO=true` 开关） |
| `scripts/push_images.sh` | 将本地 `dev` 镜像打 tag 推到私有仓库 |
| `scripts/run_local_e2e_external.sh` | 单机裸跑 python 进程做 e2e 冒烟 |

---

## 8. 一页纸 Demo（会议现场直接演示）

```bash
# 节点 B（receiver）
sudo REGISTRY=10.112.204.7:5000 ./deploy/setup-docker-daemon.sh   # 首次
docker pull 10.112.204.7:5000/video-infer-receiver:dev
TASK_ID=demo-live INFER_BACKEND=box PORT=8002 ./deploy/run-receiver.sh
curl -sf http://127.0.0.1:8002/metrics >/dev/null && echo receiver OK

# 节点 A（sender）
sudo REGISTRY=10.112.204.7:5000 ./deploy/setup-docker-daemon.sh   # 首次
docker pull 10.112.204.7:5000/video-infer-sender:dev
TASK_ID=demo-live \
RECEIVER_URL='http://<B_IP>:8002' \
VIDEO_PATH=/data/videos/test.mp4 \
UI_PORT=8012 ./deploy/run-sender.sh
curl -X POST http://127.0.0.1:8012/start_send -F task_id=demo-live

# 打开两个页面
#   Sender UI:   http://<A_IP>:8012/
#   Receiver UI: http://<B_IP>:8002/
```

看到 receiver 画面上逐帧出现推理框，并且两侧 `/metrics` 数字同步上涨，即联调通过。
