from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportingConfig:
    enabled: bool
    db_url: str
    redis_url: str
    redis_stream_key: str


@dataclass(frozen=True)
class RuntimeTaskContract:
    """
    运行时契约：把“编排层(不可随意改)”与“业务层(可调)”拆开，统一 sender/receiver/trainer 的字段形状。

    设计目标：
    1) 调度下发的关键信息（task_id、peers、reporting、task_meta）集中在 orchestration/peers 里；
    2) 与具体业务相关的参数放到 app_config/business 里；
    3) 前端 UI 可覆盖字段通过 ui_policy 显式声明（便于后续 trainer 同一套规则）。
    """

    role: str  # "sender" | "receiver" | "trainer"
    task_id: str
    node_name: str

    # 运行时“对等节点/地址”
    peers: dict[str, str] = field(default_factory=dict)

    reporting: ReportingConfig = field(default_factory=lambda: ReportingConfig(False, "", "", "task_runtime_events"))

    # 前端 UI 的策略信息（目前只表达 read-only 字段；后续可以扩展成更完整 policy）
    ui_policy: dict[str, Any] = field(default_factory=dict)

    # 业务层参数（encoder/decoder、推理/发送、采样率等），随不同 role 变动
    app_config: dict[str, Any] = field(default_factory=dict)

    # 调度下发的业务上下文（用于上报/审计/关联任务）
    task_meta: dict[str, Any] = field(default_factory=dict)

    def orchestration_summary(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "task_id": self.task_id,
            "node_name": self.node_name,
            "peers": self.peers,
            "reporting_enabled": self.reporting.enabled,
            "redis_stream_key": self.reporting.redis_stream_key,
        }


def _build_reporting(
    *,
    report_enabled: bool,
    db_url: str,
    redis_url: str,
    redis_stream_key: str,
) -> ReportingConfig:
    return ReportingConfig(
        enabled=bool(report_enabled),
        db_url=db_url or "",
        redis_url=redis_url or "",
        redis_stream_key=redis_stream_key or "task_runtime_events",
    )


def build_sender_contract(cfg: Any) -> RuntimeTaskContract:
    """
    cfg: SenderConfig（来自 app/common/config.py）
    """

    return RuntimeTaskContract(
        role="sender",
        task_id=str(cfg.task_id),
        node_name=str(cfg.node_name),
        peers={"receiver_url": str(cfg.receiver_url).rstrip("/")},
        reporting=_build_reporting(
            report_enabled=cfg.report_enabled,
            db_url=cfg.db_url,
            redis_url=cfg.redis_url,
            redis_stream_key=cfg.redis_stream_key,
        ),
        ui_policy={
            "read_only": ["task_id", "receiver_url", "target_latency_ms"],
            "editable": ["fps", "width", "height", "infer_model_name", "video"],
        },
        app_config={
            "video_path": cfg.video_path,
            "fps": cfg.fps,
            "width": cfg.width,
            "height": cfg.height,
            "target_latency_ms": cfg.target_latency_ms,
            "infer_model_name": cfg.infer_model_name,
        },
        task_meta=getattr(cfg, "task_meta", {}) or {},
    )


def build_receiver_contract(cfg: Any) -> RuntimeTaskContract:
    """
    cfg: ReceiverConfig（来自 app/common/config.py）
    """

    return RuntimeTaskContract(
        role="receiver",
        task_id=str(cfg.task_id),
        node_name=str(cfg.node_name),
        peers={},
        reporting=_build_reporting(
            report_enabled=cfg.report_enabled,
            db_url=cfg.db_url,
            redis_url=cfg.redis_url,
            redis_stream_key=cfg.redis_stream_key,
        ),
        ui_policy={
            "read_only": ["task_id"],
            "editable": [],
        },
        app_config={
            "target_latency_ms": cfg.target_latency_ms,
            "infer_backend": cfg.infer_backend,
            "yolo_model": cfg.yolo_model,
            "yolo_conf": cfg.yolo_conf,
        },
        task_meta=getattr(cfg, "task_meta", {}) or {},
    )


def build_trainer_contract(cfg: Any) -> RuntimeTaskContract:
    return RuntimeTaskContract(
        role="trainer",
        task_id=str(cfg.task_id),
        node_name=str(cfg.node_name),
        peers={
            "minio_endpoint": str(cfg.minio_endpoint),
        },
        reporting=_build_reporting(
            report_enabled=cfg.report_enabled,
            db_url=cfg.db_url,
            redis_url=cfg.redis_url,
            redis_stream_key=cfg.redis_stream_key,
        ),
        ui_policy={
            "read_only": ["task_id", "minio_endpoint", "bucket", "prefix"],
            "editable": ["epochs", "batch_size", "learning_rate", "model_name"],
        },
        app_config={
            "bucket": cfg.bucket,
            "prefix": cfg.prefix,
            "access_key": cfg.access_key,
            "secret_key": cfg.secret_key,
            "region": cfg.region,
            "epochs": cfg.epochs,
            "batch_size": cfg.batch_size,
            "learning_rate": cfg.learning_rate,
            "model_name": cfg.model_name,
            "work_dir": cfg.work_dir,
            "auto_start": cfg.auto_start,
        },
        task_meta=getattr(cfg, "task_meta", {}) or {},
    )

