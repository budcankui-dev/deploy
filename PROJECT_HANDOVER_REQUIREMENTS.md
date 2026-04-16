# 项目需求梳理与交接说明

## 1. 项目背景

本项目最初目标是实现一个可由任务分配系统动态下发并启动的“双节点视频推理任务”：

- 节点1：`sender`
  - 负责读取本地视频或前端上传的视频
  - 按指定帧率、分辨率发送到推理节点
  - 统计发送侧 RTT
  - 提供可选前端页面查看任务状态和手动触发发送

- 节点2：`receiver`
  - 负责接收视频帧
  - 对每帧执行推理并画框
  - 对外提供实时页面展示、统计指标、视频流
  - 可将运行事件和指标上报到数据库/Redis

后续需求扩展为“多任务平台”，除视频推理外，还预留了：

- 两节点训练任务
  - 一个 MinIO 节点负责数据存储
  - 一个 `trainer` 节点负责训练并展示训练进度页面

当前阶段的实际重点仍然是：

1. 先把视频推理任务的 sender / receiver 两个节点跑通
2. 确保本地直接运行、前端可用、参数动态注入、后续再逐步稳定 Docker 化

## 2. 当前明确需求

### 2.1 视频推理任务

视频推理任务由两个节点组成：

- `sender`
- `receiver`

运行时由外部任务分配系统动态提供：

- `task_id`
- 各节点 IP
- 各节点端口
- 任务运行参数
- 数据库信息
- Redis 信息

这些参数不应在代码中写死。

### 2.2 参数分层要求

项目中已经明确区分两类参数：

- 编排层参数
  - 由调度/任务分配系统下发
  - UI 中通常只读，不允许业务人员随意改
  - 例如：
    - `task_id`
    - `receiver_url`
    - 节点 host / port
    - 数据库地址
    - Redis 地址
    - peers 信息

- 业务层参数
  - 跟具体任务执行相关
  - 可由初始下发参数决定，也可允许页面局部覆盖
  - 例如：
    - `fps`
    - `width`
    - `height`
    - 推理模型别名
    - 训练 epoch / batch size / learning rate

当前代码已通过 `runtime/task_contract.py` 对这类信息做了统一抽象。

### 2.3 视频推理展示要求

`receiver` 页面需要具备：

- 展示接收到并推理后的画面
- 实时显示任务 `task_id`
- 展示：
  - 推理均值
  - 推理 P95
  - RTT 均值
  - RTT P95
  - 最新 RTT
  - 已处理帧数
- 支持按 `task_id` 查看统计
- 页面应是响应式的，不依赖手动刷新

`sender` 页面需要具备：

- 展示当前任务 `task_id`
- 展示当前发送参数
- 展示发送统计：
  - 已发送帧
  - 成功帧
  - 失败帧
  - RTT 均值
  - RTT P95
- 支持选择视频文件并点击开始发送
- 当任务正在发送时，不允许重复点击启动
- 未选择视频时，默认使用启动参数中的视频路径

### 2.4 推理要求

推理逻辑要求如下：

- 优先使用 YOLO 系列模型进行画框
- 允许通过别名配置模型，如：
  - `yolov8`
  - `yolov9`
- 如果 YOLO 不可用、安装失败或单帧推理失败：
  - 自动退化为随机框兜底
  - 保证整条链路可继续运行

当前代码已支持这种 fallback 思路。

### 2.5 时延统计要求

用户已明确要求跨机器场景下不要使用不可靠的“双端时间差”。

当前采用的指标定义：

- `infer_ms`
  - receiver 端单帧推理耗时

- `rtt_ms` / `e2e_rtt_ms`
  - sender 发起请求到收到 receiver 响应的往返耗时
  - 这是当前主展示时延指标

### 2.6 上报要求

程序需支持将事件和运行指标上报到：

- MySQL
- Redis Stream

数据库表设计要兼容未来多节点任务，而不只服务于视频推理。

当前仓库内已有：

- `sql/mysql_task_schema.sql`

用于多节点任务通用建表。

同时，用户在本地联调时也希望能关闭上报：

- `report_enabled=false`

### 2.7 Docker / 部署要求

部署要求不是“一个镜像兼容全部逻辑后再运行时选择”，而是更倾向于：

- sender 一个镜像
- receiver 一个镜像
- trainer 一个镜像
- MinIO 使用官方镜像

但在当前阶段，优先级是：

1. 宿主机直接跑通 sender / receiver
2. 再确认 sender / receiver Docker 化
3. trainer 和 MinIO 作为下一阶段完善

## 3. 当前代码结构（交接时应了解）

### 3.1 运行时层

- `runtime/task_contract.py`
  - 统一描述 task 的运行时契约
  - 包含：
    - `task_id`
    - `role`
    - `node_name`
    - `peers`
    - `reporting`
    - `ui_policy`
    - `app_config`
    - `task_meta`

- `runtime/report_client.py`
  - 运行事件上报客户端封装

- `runtime/app_args.py`
  - 参数辅助解析工具

- `runtime/web_static.py`
  - FastAPI 提供 SPA 静态资源与 fallback 的通用逻辑

### 3.2 视频推理应用层

- `apps/video_infer/sender_app.py`
- `apps/video_infer/receiver_app.py`
- `apps/video_infer/core/`

旧的：

- `app/sender.py`
- `app/receiver.py`

目前只是兼容导出入口，不建议继续作为主开发文件。

### 3.3 训练应用层

- `apps/model_train/trainer_app.py`

目前是预留骨架，主要用于：

- 训练任务接口占位
- UI 占位
- MinIO 拉取/写回占位

训练具体算法、真实数据集、GPU 训练流程仍未真正完善。

### 3.4 前端

前端目录：

- `frontend/`

当前为一个 Vue SPA，包含：

- `SenderPage.vue`
- `ReceiverPage.vue`
- `TrainerPage.vue`

后端通过 `WEB_DIR` 指向 `frontend/dist` 后，可直接提供页面。

## 4. 当前实现状态

### 已完成

- 已完成 sender / receiver 的基础业务逻辑
- 已完成 runtime task contract 抽象
- 已完成 sender / receiver 从内联 HTML 到 SPA 静态资源的切换
- 已完成 sender / receiver 拆分到 `apps/video_infer/*`
- 已增加 trainer 的基础骨架
- 已增加 role Dockerfile
- 已补充本地与 Docker 测试脚本

### 当前已知有效的开发优先级

目前应优先验证：

1. 不封装镜像时，宿主机直接运行 sender / receiver 是否正常
2. 前端打包后，receiver / sender 页面是否正常打开
3. sender 点击开始发送后，receiver 是否能看到帧和指标变化
4. 确认默认端口不要与 Redis 看板冲突

### 当前已知注意点

- `8001` 可能与 Redis 看板冲突
  - 当前默认 receiver 端口已调整为 `8002`

- Docker 构建时，如果依赖包含 `ultralytics`
  - 可能会拉取非常大的依赖（如 torch）
  - 国内源即便可用，也可能非常慢
  - 当前已将视频推理 Docker 默认依赖拆轻，YOLO 安装改为可选

- 当前最稳的验证路径是：
  - 先本地运行
  - 再 Docker 化

## 5. 推荐测试方式（交接时优先走这个）

### 5.1 本地直接运行（推荐优先）

步骤：

1. 构建前端：
   - 进入 `frontend/`
   - `npm install`
   - `npm run build`

2. 设置：
   - `WEB_DIR=<repo>/frontend/dist`

3. 启动 receiver：
   - `python -m app.start --role receiver --port 8002 ...`

4. 启动 sender：
   - `python -m app.start --role sender --receiver-url http://127.0.0.1:8002 --ui-port 8012 ...`

5. 打开：
   - receiver UI：`http://127.0.0.1:8002/`
   - sender UI：`http://127.0.0.1:8012/`

### 5.2 Docker 运行

Docker 流程目前适合作为第二阶段验证，而不是第一优先级。

如果使用 Docker，需要特别注意：

- pip 源速度
- 大包下载
- 容器端口冲突
- 视频文件挂载路径

## 6. 建议后续继续完善的内容

### 高优先级

- 更新 `README.md`
  - 目前 README 中仍有旧结构/旧端口/旧镜像方式残留

- 把“本地直跑流程”写成正式脚本
  - 例如同时自动 build 前端、设置 `WEB_DIR`、启动 receiver/sender

- 统一前端默认 API 地址逻辑
  - 尽量优先使用 `window.location.origin`
  - 减少手动切换 API 地址的成本

- 进一步验证 sender / receiver 页面在真实浏览器里的行为
  - 是否需要手动刷新
  - SSE 是否稳定
  - 视频是否正常显示

### 中优先级

- trainer 真正接入训练流程
- MinIO 与 trainer 的真实端到端联调
- 将数据库/Redis 上报字段进一步规范化
- 梳理 `ui_policy` 在前端中的真正约束能力

### 低优先级

- 统一 shell 脚本参数风格
- 清理历史遗留 Dockerfile / 脚本
- 增加更明确的 docs 目录

## 7. 交接结论

如果后续由其他人接手，本项目当前最重要的认知是：

- 这不是一个单纯的“视频推理 demo”
- 它已经朝“多任务平台”方向做了 runtime 和 role 拆分
- 但真正稳定可交付的核心仍然是视频推理 sender / receiver 两节点
- trainer / MinIO 目前更多是预留骨架，而不是完整产品能力

因此，后续继续推进时，建议遵循以下顺序：

1. 先稳定本地 sender / receiver 直跑体验
2. 再稳定 sender / receiver Docker 体验
3. 再补 trainer / MinIO 的真实训练链路
4. 最后再统一更新 README、脚本和部署文档

## 8. 任务运行上报查询（intent，已落地）

当前推荐策略：

- Redis：保留详细事件流（逐帧、排障）
- MySQL `intent`：保留任务状态和聚合指标（列表展示友好）

### 8.1 首次初始化

必须先执行 `sql/mysql_task_schema.sql`，创建 `intent` 库以及两张表：

- `task_runtime_status`
- `task_runtime_metrics`

### 8.2 启动参数（Redis + MySQL 同时上报）

建议优先使用拆分参数方式：

- `--db-type mysql`
- `--db-host ...`
- `--db-port ...`
- `--db-user ...`
- `--db-password ...`
- `--db-name intent`

避免手写完整 `--db-url` 时因特殊字符（如 `@`）导致连接串解析问题。

### 8.3 任务分配系统常用查询 SQL

任务状态列表：

```sql
SELECT task_id, state, started_at, ended_at, last_update_at
FROM intent.task_runtime_status
ORDER BY last_update_at DESC
LIMIT 100;
```

任务聚合指标：

```sql
SELECT
  task_id, role, sample_count,
  infer_avg_ms, infer_p95_ms,
  rtt_avg_ms, rtt_p95_ms, latest_rtt_ms,
  meet_target_count, meet_target_ratio, target_latency_ms,
  updated_at
FROM intent.task_runtime_metrics
WHERE task_id = 'task-demo-001'
ORDER BY role;
```

联表展示（任务分配系统详情页）：

```sql
SELECT
  s.task_id, s.state, s.started_at, s.ended_at, s.last_update_at,
  m.role, m.sample_count, m.infer_avg_ms, m.infer_p95_ms,
  m.rtt_avg_ms, m.rtt_p95_ms, m.latest_rtt_ms,
  m.meet_target_count, m.meet_target_ratio, m.target_latency_ms
FROM intent.task_runtime_status s
LEFT JOIN intent.task_runtime_metrics m ON s.task_id = m.task_id
ORDER BY s.last_update_at DESC, m.role ASC
LIMIT 200;
```

### 8.4 Redis 事件流查询

事件流默认 key：`task_runtime_events`  
常见事件：`sender_start`、`receiver_start`、`frame_inferred`、`frame_rtt`、`frame_sent_ack`、`ui_metrics`

## 9. IPv6 下发与节点互联（重点）

任务分配系统下发 IPv6 地址时，最容易出错的是 URL 拼接格式。

### 9.1 已完成的兼容增强

已在 `app/common/config.py` 增强：

- `receiver_url` 自动规范化，兼容以下输入：
  - `http://[240e:xxxx::1]:8002`（标准）
  - `240e:xxxx::1`（自动补全为 `http://[...]:8002`）
  - `[240e:xxxx::1]:8002`（自动补全 scheme）
- MySQL 的 `db_host` 若为 IPv6，会自动补 `[]` 再拼 URL，避免 SQLAlchemy 连接串解析失败。

### 9.2 强烈建议的下发格式

为避免不同组件对 URL 解析差异，调度系统建议统一下发“标准 IPv6 URL”：

- receiver peer URL：`http://[IPv6]:PORT`
- Redis URL：`redis://:password@[IPv6]:6379/0`
- 若需要 `--db-url`：`mysql+pymysql://user:pwd@[IPv6]:3306/intent?charset=utf8mb4`

### 9.3 启动示例（IPv6）

receiver：

```bash
python -m app.start --role receiver \
  --task-id task-ipv6-001 \
  --host :: \
  --port 8002 \
  --report-enabled true \
  --db-type mysql \
  --db-host 240e:xxxx::10 \
  --db-port 3306 \
  --db-user root \
  --db-password '***' \
  --db-name intent \
  --redis-url 'redis://:***@[240e:xxxx::20]:6379/0'
```

sender：

```bash
python -m app.start --role sender \
  --task-id task-ipv6-001 \
  --receiver-url 'http://[240e:xxxx::30]:8002' \
  --ui-port 8012 \
  --report-enabled true \
  --db-type mysql \
  --db-host 240e:xxxx::10 \
  --db-port 3306 \
  --db-user root \
  --db-password '***' \
  --db-name intent \
  --redis-url 'redis://:***@[240e:xxxx::20]:6379/0'
```

### 9.4 IPv6 排障检查清单

- `receiver_url` 是否包含 `[]`（或由程序自动规范化后是否正确）
- receiver 是否监听在 `--host ::`（或你指定的 IPv6 绑定地址）
- 节点 OS/防火墙是否放行 IPv6 入站端口（如 8002/8012）
- Redis/MySQL 是否实际监听 IPv6（`ss -tlnp` 检查）
- 若跨网段，确认路由/NAT64/ACL 策略允许该 IPv6 段互通

### 9.5 一键启动前自检脚本

已新增脚本：

- `scripts/preflight_ipv6_check.sh`

能力：

- 校验 `RECEIVER_URL` 格式（含 IPv6）
- 检查 receiver TCP/HTTP 可达性
- 可选检查 MySQL 连通（`DB_HOST` 非空时）
- 可选检查 Redis 连通（`REDIS_URL` 非空时）

示例：

```bash
export PATH="/data/hdd1/cyb/yes/envs/video_infer/bin:$PATH"

RECEIVER_URL='http://[240e:xxxx::30]:8002' \
RECEIVER_HOST='::' \
RECEIVER_PORT=8002 \
DB_HOST='240e:xxxx::10' DB_PORT=3306 DB_USER='root' DB_PASSWORD='***' DB_NAME='intent' \
REDIS_URL='redis://:***@[240e:xxxx::20]:6379/0' \
scripts/preflight_ipv6_check.sh
```

说明：

- 本脚本适合调度任务下发后、真正启动 sender/receiver 前执行一次。
- 若只想检查 receiver，可不传 `DB_HOST` / `REDIS_URL`，脚本会自动 `SKIP` 对应检查。

