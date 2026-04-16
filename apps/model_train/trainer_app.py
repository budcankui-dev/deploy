from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from runtime.app_args import env_json
from runtime.report_client import ReportClient
from runtime.task_contract import RuntimeTaskContract, build_trainer_contract
from runtime.web_static import serve_spa

try:
    from minio import Minio
except Exception:  # pragma: no cover
    Minio = None


@dataclass
class TrainerConfig:
    task_id: str
    node_name: str
    report_enabled: bool
    db_url: str
    redis_url: str
    redis_stream_key: str
    task_meta: dict
    host: str
    port: int
    minio_endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    prefix: str
    region: str
    epochs: int
    batch_size: int
    learning_rate: float
    model_name: str
    work_dir: str
    auto_start: bool


def parse_bool(raw: str | bool | None, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def parse_trainer_args() -> TrainerConfig:
    parser = argparse.ArgumentParser(description="model trainer node")
    parser.add_argument("--task-id", default=os.getenv("TASK_ID", "train-demo"))
    parser.add_argument("--node-name", default=os.getenv("NODE_NAME", "trainer"))
    parser.add_argument("--report-enabled", default=os.getenv("REPORT_ENABLED", "true"))
    parser.add_argument("--db-url", default=os.getenv("DB_URL", "sqlite:///./data/task_runtime.db"))
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", ""))
    parser.add_argument("--redis-stream-key", default=os.getenv("REDIS_STREAM_KEY", "task_runtime_events"))
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8013")))
    parser.add_argument("--minio-endpoint", default=os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000"))
    parser.add_argument("--access-key", default=os.getenv("MINIO_ACCESS_KEY", "minioadmin"))
    parser.add_argument("--secret-key", default=os.getenv("MINIO_SECRET_KEY", "minioadmin"))
    parser.add_argument("--bucket", default=os.getenv("MINIO_BUCKET", "datasets"))
    parser.add_argument("--prefix", default=os.getenv("MINIO_PREFIX", "tasks"))
    parser.add_argument("--region", default=os.getenv("MINIO_REGION", ""))
    parser.add_argument("--epochs", type=int, default=int(os.getenv("EPOCHS", "3")))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("BATCH_SIZE", "8")))
    parser.add_argument("--learning-rate", type=float, default=float(os.getenv("LEARNING_RATE", "0.001")))
    parser.add_argument("--model-name", default=os.getenv("MODEL_NAME", "demo-classifier"))
    parser.add_argument("--work-dir", default=os.getenv("WORK_DIR", "./data/train_runs"))
    parser.add_argument("--auto-start", default=os.getenv("AUTO_START", "true"))
    parser.add_argument("--task-meta", default=os.getenv("TASK_META", "{}"))
    args = parser.parse_args()
    return TrainerConfig(
        task_id=args.task_id,
        node_name=args.node_name,
        report_enabled=parse_bool(args.report_enabled, True),
        db_url=args.db_url,
        redis_url=args.redis_url,
        redis_stream_key=args.redis_stream_key,
        task_meta=env_json("TASK_META", {}) or (json.loads(args.task_meta) if args.task_meta else {}),
        host=args.host,
        port=args.port,
        minio_endpoint=args.minio_endpoint,
        access_key=args.access_key,
        secret_key=args.secret_key,
        bucket=args.bucket,
        prefix=args.prefix,
        region=args.region,
        epochs=max(args.epochs, 1),
        batch_size=max(args.batch_size, 1),
        learning_rate=max(args.learning_rate, 0.0),
        model_name=args.model_name,
        work_dir=args.work_dir,
        auto_start=parse_bool(args.auto_start, True),
    )


class TrainerState:
    def __init__(self, contract: RuntimeTaskContract):
        self.contract = contract
        self.running = False
        self.finished = False
        self.last_error = ""
        self.latest_epoch = 0
        self.loss_history: list[float] = []
        self.message = "idle"
        self.download_dir = Path(contract.app_config["work_dir"]) / contract.task_id / "dataset"
        self.output_dir = Path(contract.app_config["work_dir"]) / contract.task_id / "outputs"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.worker: threading.Thread | None = None
        self.lock = threading.Lock()


trainer_app = FastAPI(title="Model Trainer Node")
cors_origins = [x.strip() for x in os.getenv("FRONTEND_ORIGINS", "*").split(",") if x.strip()]
trainer_app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
trainer_state: TrainerState | None = None
trainer_contract: RuntimeTaskContract | None = None
reporter: ReportClient | None = None


def _minio_client(contract: RuntimeTaskContract):
    if Minio is None:
        return None
    endpoint = contract.peers["minio_endpoint"].replace("http://", "").replace("https://", "")
    secure = contract.peers["minio_endpoint"].startswith("https://")
    return Minio(
        endpoint,
        access_key=contract.app_config["access_key"],
        secret_key=contract.app_config["secret_key"],
        region=contract.app_config.get("region") or None,
        secure=secure,
    )


def _write_placeholder_outputs(state: TrainerState):
    summary = {
        "task_id": state.contract.task_id,
        "bucket": state.contract.app_config["bucket"],
        "prefix": state.contract.app_config["prefix"],
        "loss_history": state.loss_history,
        "model_name": state.contract.app_config["model_name"],
    }
    (state.output_dir / "train-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (state.output_dir / "model-placeholder.txt").write_text("placeholder model artifact\n")


def _sync_minio_data(state: TrainerState):
    client = _minio_client(state.contract)
    if client is None:
        return
    bucket = state.contract.app_config["bucket"]
    prefix = f"{state.contract.app_config['prefix'].rstrip('/')}/{state.contract.task_id}/images/"
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        found = False
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
            if obj.is_dir:
                continue
            found = True
            rel = obj.object_name[len(prefix) :]
            target = state.download_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            client.fget_object(bucket, obj.object_name, str(target))
        if not found:
            sample = state.download_dir / "README.txt"
            sample.write_text("No dataset uploaded yet. This is a placeholder dataset.\n")
    except Exception as exc:
        state.last_error = f"minio download failed: {exc}"


def _upload_outputs(state: TrainerState):
    client = _minio_client(state.contract)
    if client is None:
        return
    bucket = state.contract.app_config["bucket"]
    base_prefix = f"{state.contract.app_config['prefix'].rstrip('/')}/{state.contract.task_id}/outputs"
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        for path in state.output_dir.rglob("*"):
            if path.is_file():
                object_name = f"{base_prefix}/{path.relative_to(state.output_dir).as_posix()}"
                client.fput_object(bucket, object_name, str(path))
    except Exception as exc:
        state.last_error = f"minio upload failed: {exc}"


def run_training(state: TrainerState):
    global reporter
    contract = state.contract
    with state.lock:
        state.running = True
        state.finished = False
        state.last_error = ""
        state.loss_history = []
        state.latest_epoch = 0
        state.message = "preparing dataset"
    if contract.reporting.enabled and reporter is None:
        reporter = ReportClient(contract.reporting.db_url, redis_url=contract.reporting.redis_url, redis_stream_key=contract.reporting.redis_stream_key)
    if reporter:
        reporter.report(contract.task_id, contract.node_name, "trainer_start", payload={"task_meta": contract.task_meta, "app_config": contract.app_config})

    try:
        _sync_minio_data(state)
        for epoch in range(1, int(contract.app_config["epochs"]) + 1):
            time.sleep(1.0)
            loss = round(1.0 / epoch, 4)
            with state.lock:
                state.latest_epoch = epoch
                state.loss_history.append(loss)
                state.message = f"training epoch {epoch}/{contract.app_config['epochs']}"
            if reporter:
                reporter.report(contract.task_id, contract.node_name, "trainer_epoch", frame_id=epoch, latency_ms=loss, payload={"loss": loss})
        _write_placeholder_outputs(state)
        _upload_outputs(state)
        with state.lock:
            state.message = "finished"
            state.finished = True
    except Exception as exc:
        with state.lock:
            state.last_error = str(exc)
            state.message = "failed"
        if reporter:
            reporter.report(contract.task_id, contract.node_name, "trainer_error", payload={"error": str(exc)})
    finally:
        with state.lock:
            state.running = False
        if reporter:
            reporter.report(contract.task_id, contract.node_name, "trainer_stop", payload={"message": state.message})


@trainer_app.get("/metrics")
def metrics():
    if trainer_state is None:
        return JSONResponse({"ok": False, "error": "trainer not initialized"}, status_code=500)
    with trainer_state.lock:
        return {
            "task_id": trainer_state.contract.task_id,
            "running": trainer_state.running,
            "finished": trainer_state.finished,
            "last_error": trainer_state.last_error,
            "latest_epoch": trainer_state.latest_epoch,
            "loss_history": trainer_state.loss_history,
            "message": trainer_state.message,
            "download_dir": str(trainer_state.download_dir),
            "output_dir": str(trainer_state.output_dir),
            "minio_endpoint": trainer_state.contract.peers["minio_endpoint"],
            "bucket": trainer_state.contract.app_config["bucket"],
            "prefix": trainer_state.contract.app_config["prefix"],
            "model_name": trainer_state.contract.app_config["model_name"],
            "ui_policy": trainer_state.contract.ui_policy,
        }


@trainer_app.get("/metrics_sse")
async def metrics_sse():
    async def event_stream():
        while True:
            yield f"data: {json.dumps(metrics(), ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@trainer_app.post("/start_train")
def start_train(
    epochs: int = Form(0),
    batch_size: int = Form(0),
    learning_rate: float = Form(0.0),
    model_name: str = Form(""),
):
    if trainer_state is None:
        return JSONResponse({"ok": False, "error": "trainer not initialized"}, status_code=500)
    with trainer_state.lock:
        if trainer_state.running:
            return JSONResponse({"ok": False, "error": "trainer is running"}, status_code=409)
        if epochs > 0:
            trainer_state.contract.app_config["epochs"] = epochs
        if batch_size > 0:
            trainer_state.contract.app_config["batch_size"] = batch_size
        if learning_rate > 0:
            trainer_state.contract.app_config["learning_rate"] = learning_rate
        if model_name.strip():
            trainer_state.contract.app_config["model_name"] = model_name.strip()
        trainer_state.worker = threading.Thread(target=run_training, args=(trainer_state,), daemon=True)
        trainer_state.worker.start()
    return {"ok": True, "task_id": trainer_state.contract.task_id}


@trainer_app.get("/")
def index():
    return serve_spa(os.getenv("WEB_DIR", "/web"), "index.html", ("metrics", "metrics_sse", "start_train"))


@trainer_app.get("/{path:path}", include_in_schema=False)
def trainer_spa(path: str):
    return serve_spa(os.getenv("WEB_DIR", "/web"), path, ("metrics", "metrics_sse", "start_train"))


def main():
    global trainer_state, trainer_contract
    cfg = parse_trainer_args()
    trainer_contract = build_trainer_contract(cfg)
    trainer_state = TrainerState(trainer_contract)
    if cfg.auto_start:
        trainer_state.worker = threading.Thread(target=run_training, args=(trainer_state,), daemon=True)
        trainer_state.worker.start()
    uvicorn.run(trainer_app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()

