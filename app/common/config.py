from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Optional, Union
from urllib.parse import quote_plus, urlparse, urlunparse


@dataclass
class CommonConfig:
    task_id: str
    report_enabled: bool
    target_latency_ms: float | None
    db_url: str
    redis_url: str
    redis_stream_key: str
    task_meta: dict


@dataclass
class SenderConfig(CommonConfig):
    video_path: str
    receiver_url: str
    fps: float
    width: int
    height: int
    node_name: str
    ui_host: str
    ui_port: int
    infer_model_name: str


@dataclass
class ReceiverConfig(CommonConfig):
    host: str
    port: int
    node_name: str
    infer_backend: str
    yolo_model: str
    yolo_conf: float


def _base_parser(role: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{role} node")
    parser.add_argument("--task-id", default=os.getenv("TASK_ID", "task-demo"))
    parser.add_argument("--report-enabled", default=os.getenv("REPORT_ENABLED", "true"))
    parser.add_argument("--target-latency-ms", type=float, default=float(os.getenv("TARGET_LATENCY_MS", "0")))
    parser.add_argument("--db-url", default=os.getenv("DB_URL", ""))
    parser.add_argument("--db-type", default=os.getenv("DB_TYPE", "mysql"), choices=["mysql", "sqlite"])
    parser.add_argument("--db-host", default=os.getenv("DB_HOST", "10.112.204.7"))
    parser.add_argument("--db-port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    parser.add_argument("--db-user", default=os.getenv("DB_USER", "root"))
    parser.add_argument("--db-password", default=os.getenv("DB_PASSWORD", ""))
    parser.add_argument("--db-name", default=os.getenv("DB_NAME", "intent"))
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", ""))
    parser.add_argument("--redis-stream-key", default=os.getenv("REDIS_STREAM_KEY", "task_runtime_events"))
    parser.add_argument("--task-meta", default=os.getenv("TASK_META", "{}"))
    parser.add_argument("--node-name", default=os.getenv("NODE_NAME", role))
    return parser


def parse_sender_args() -> SenderConfig:
    parser = _base_parser("sender")
    default_video = os.getenv("VIDEO_PATH", "").strip()
    if not default_video:
        # Prefer real video shipped/placed by user under ./data/test.mp4 when running locally.
        if os.path.isfile("./data/test.mp4"):
            default_video = "./data/test.mp4"
        else:
            default_video = "/app/data/test.mp4"
    parser.add_argument("--video-path", default=default_video)
    parser.add_argument("--receiver-url", default=os.getenv("RECEIVER_URL", "http://127.0.0.1:8002"))
    parser.add_argument("--fps", type=float, default=float(os.getenv("FPS", "10")))
    parser.add_argument("--width", type=int, default=int(os.getenv("WIDTH", "640")))
    parser.add_argument("--height", type=int, default=int(os.getenv("HEIGHT", "360")))
    parser.add_argument("--ui-host", default=os.getenv("SENDER_UI_HOST", "0.0.0.0"))
    parser.add_argument("--ui-port", type=int, default=int(os.getenv("SENDER_UI_PORT", "0")))
    parser.add_argument("--infer-model-name", default=os.getenv("INFER_MODEL_NAME", "yolov8"))
    args = parser.parse_args()
    db_url = _build_db_url_from_args(args)
    return SenderConfig(
        task_id=args.task_id,
        report_enabled=_parse_bool(args.report_enabled, True),
        target_latency_ms=args.target_latency_ms if args.target_latency_ms > 0 else None,
        db_url=db_url,
        redis_url=args.redis_url,
        redis_stream_key=args.redis_stream_key,
        task_meta=_parse_task_meta(args.task_meta),
        video_path=args.video_path,
        receiver_url=_normalize_receiver_url(args.receiver_url),
        fps=max(args.fps, 0.1),
        width=max(args.width, 1),
        height=max(args.height, 1),
        node_name=args.node_name,
        ui_host=args.ui_host,
        ui_port=max(args.ui_port, 0),
        infer_model_name=args.infer_model_name,
    )


def parse_receiver_args() -> ReceiverConfig:
    parser = _base_parser("receiver")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8002")))
    parser.add_argument("--infer-backend", default=os.getenv("INFER_BACKEND", "yolo"), choices=["yolo", "box"])
    parser.add_argument("--yolo-model", default=os.getenv("YOLO_MODEL", "yolov8"))
    parser.add_argument("--yolo-conf", type=float, default=float(os.getenv("YOLO_CONF", "0.25")))
    args = parser.parse_args()
    db_url = _build_db_url_from_args(args)
    return ReceiverConfig(
        task_id=args.task_id,
        report_enabled=_parse_bool(args.report_enabled, True),
        target_latency_ms=args.target_latency_ms if args.target_latency_ms > 0 else None,
        db_url=db_url,
        redis_url=args.redis_url,
        redis_stream_key=args.redis_stream_key,
        task_meta=_parse_task_meta(args.task_meta),
        host=args.host,
        port=args.port,
        node_name=args.node_name,
        infer_backend=args.infer_backend,
        yolo_model=args.yolo_model,
        yolo_conf=max(min(args.yolo_conf, 1.0), 0.01),
    )


def _build_db_url_from_args(args: argparse.Namespace) -> str:
    if args.db_url and args.db_url.strip():
        return args.db_url
    if args.db_type.lower() == "mysql":
        user = quote_plus(args.db_user or "root")
        pwd = quote_plus(args.db_password or "")
        host = _normalize_url_host(args.db_host or "127.0.0.1")
        port = args.db_port or 3306
        name = args.db_name or "video_infer"
        return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{name}?charset=utf8mb4"
    return "sqlite:///./data/task_runtime.db"


def _normalize_receiver_url(raw: str) -> str:
    """
    Normalize receiver URL for IPv4/IPv6.
    Supports:
    - http://127.0.0.1:8002
    - http://[240e:xx::1]:8002
    - 240e:xx::1 (auto-fill scheme/port)
    - [240e:xx::1]:8002
    """
    value = (raw or "").strip()
    if not value:
        return "http://127.0.0.1:8002"

    # If user passes pure host (v4/v6) without scheme, add scheme and default port.
    if "://" not in value:
        if value.startswith("[") and "]" in value:
            if ":" in value.split("]", 1)[1]:
                return f"http://{value}".rstrip("/")
            return f"http://{value}:8002".rstrip("/")
        if value.count(":") >= 2:
            return f"http://[{value}]:8002".rstrip("/")
        if ":" in value:
            return f"http://{value}".rstrip("/")
        return f"http://{value}:8002".rstrip("/")

    parsed = urlparse(value)
    scheme = parsed.scheme or "http"
    path = parsed.path or ""
    netloc = parsed.netloc
    if not netloc and parsed.path:
        # Handle malformed forms like "http://240e::1:8002" parsed strangely.
        netloc = parsed.path
        path = ""

    if ":" in netloc and not netloc.startswith("["):
        # likely IPv6 without brackets
        if netloc.count(":") >= 2:
            if netloc.rfind(":") > netloc.find(":"):
                host_part, maybe_port = netloc.rsplit(":", 1)
                if maybe_port.isdigit():
                    netloc = f"[{host_part}]:{maybe_port}"
                else:
                    netloc = f"[{netloc}]"
            else:
                netloc = f"[{netloc}]"
    normalized = urlunparse((scheme, netloc, path, "", "", ""))
    return normalized.rstrip("/")


def _normalize_url_host(host: str) -> str:
    """
    Normalize host segment used in URL authority for DB URL.
    IPv6 host in URL must be bracketed.
    """
    value = (host or "").strip()
    if ":" in value and not value.startswith("["):
        return f"[{value}]"
    return value


def _parse_task_meta(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"raw": val}
    except Exception:
        return {"raw": raw}


def _parse_bool(raw: str | bool | None, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    val = str(raw).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return default
