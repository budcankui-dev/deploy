from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path
from threading import Lock, Thread
from typing import Any

import cv2
import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.common.config import SenderConfig, parse_sender_args
from apps.video_infer.core.frame_io import encode_frame_jpeg
from apps.video_infer.core.metrics import avg, p95
from runtime.report_client import ReportClient
from runtime.task_contract import RuntimeTaskContract, build_sender_contract
from runtime.web_static import serve_spa

SUPPORTED_MODEL_ALIASES = ["yolov8", "yolov9"]
AUTO_VIDEO_FILENAME = "auto_generated.mp4"


def _auto_video_path(default_video_path: str) -> str:
    # Keep auto-generated video in ./data to avoid clobbering user's real data video.
    # Local: ./data/auto_generated.mp4
    # Docker: /app/data/auto_generated.mp4
    cwd_path = os.path.join(".", "data", AUTO_VIDEO_FILENAME)
    if os.path.isfile(cwd_path):
        return cwd_path
    # Fallback: alongside default video if someone placed it there manually
    base_dir = os.path.dirname(default_video_path) or "."
    return os.path.join(base_dir, AUTO_VIDEO_FILENAME)


def _list_videos(default_video_path: str) -> list[dict[str, Any]]:
    auto_path = _auto_video_path(default_video_path)
    real_name = os.path.basename(default_video_path) or "test.mp4"
    return [
        {"id": "real", "label": f"真实视频（{real_name}）", "path": default_video_path, "exists": os.path.isfile(default_video_path)},
        {"id": "auto", "label": f"合成视频（{AUTO_VIDEO_FILENAME}）", "path": auto_path, "exists": os.path.isfile(auto_path)},
    ]


class SenderState:
    def __init__(self, cfg: SenderConfig):
        self.contract: RuntimeTaskContract = build_sender_contract(cfg)
        self.task_id = self.contract.task_id
        self.receiver_url = self.contract.peers.get("receiver_url", "")
        self.fps = float(self.contract.app_config.get("fps", cfg.fps))
        self.width = int(self.contract.app_config.get("width", cfg.width))
        self.height = int(self.contract.app_config.get("height", cfg.height))
        self.video_path = str(self.contract.app_config.get("video_path", cfg.video_path))
        self.default_video_path = self.video_path
        self.target_latency_ms = self.contract.app_config.get("target_latency_ms")
        self.infer_model_name = str(self.contract.app_config.get("infer_model_name", cfg.infer_model_name))
        self.sent_frames = 0
        self.success_frames = 0
        self.error_frames = 0
        self.latest_rtt_ms = None
        self.latest_infer_ms = None
        self.rtt_ms_window = deque(maxlen=5000)
        self.started_at = time.time()
        self.finished = False
        self.stopped = False
        self.stop_requested = False
        self.last_error = ""
        self.running = False
        self.worker: Thread | None = None
        self.upload_dir = Path("./uploads")
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()


sender_app = FastAPI(title="Video Sender Node")
cors_origins = [x.strip() for x in os.getenv("FRONTEND_ORIGINS", "*").split(",") if x.strip()]
sender_app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
sender_state: SenderState | None = None
ui_reporter: ReportClient | None = None
_ui_last_report_ts: dict[str, float] = {}


def _maybe_report_sender_ui_metrics(snapshot: dict[str, Any]) -> None:
    """
    Report sender UI-visible aggregated metrics snapshot to Redis/DB (redis-only supported).
    Rate-limited per task_id to avoid flooding (frontend polls every 1s).
    """
    global ui_reporter
    if sender_state is None:
        return
    contract = sender_state.contract
    if not contract.reporting.enabled:
        return
    task_id = str(snapshot.get("task_id") or contract.task_id or "")
    if not task_id:
        return
    now = time.time()
    last = _ui_last_report_ts.get(task_id, 0.0)
    if now - last < 5.0:
        return
    _ui_last_report_ts[task_id] = now

    if ui_reporter is None:
        ui_reporter = ReportClient(
            contract.reporting.db_url,
            redis_url=contract.reporting.redis_url,
            redis_stream_key=contract.reporting.redis_stream_key,
        )

    payload = {
        "role": "sender",
        "source": "metrics",
        "video_path": snapshot.get("video_path"),
        "default_video_path": snapshot.get("default_video_path"),
        "fps": snapshot.get("fps"),
        "width": snapshot.get("width"),
        "height": snapshot.get("height"),
        "infer_model_name": snapshot.get("infer_model_name"),
        "sent_frames": snapshot.get("sent_frames"),
        "success_frames": snapshot.get("success_frames"),
        "error_frames": snapshot.get("error_frames"),
        "latest_rtt_ms": snapshot.get("latest_rtt_ms"),
        "latest_infer_ms": snapshot.get("latest_infer_ms"),
        "rtt_avg_ms": snapshot.get("rtt_avg_ms"),
        "rtt_p95_ms": snapshot.get("rtt_p95_ms"),
        "running": snapshot.get("running"),
        "finished": snapshot.get("finished"),
        "stopped": snapshot.get("stopped"),
        "last_error": snapshot.get("last_error"),
    }
    ui_reporter.report(task_id, contract.node_name, "ui_metrics", payload=payload)


def send_frames(contract: RuntimeTaskContract, state: SenderState | None = None) -> None:
    reporter = None
    if contract.reporting.enabled:
        reporter = ReportClient(
            contract.reporting.db_url,
            redis_url=contract.reporting.redis_url,
            redis_stream_key=contract.reporting.redis_stream_key,
        )
        reporter.report(contract.task_id, contract.node_name, "sender_start", payload={"task_meta": contract.task_meta})

    video_path = str(contract.app_config["video_path"])
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        if reporter:
            reporter.report(contract.task_id, contract.node_name, "sender_error", payload={"reason": "video_open_failed"})
        raise RuntimeError(f"无法打开视频: {video_path}")

    frame_interval = 1.0 / float(contract.app_config["fps"])
    frame_id = 0
    rtt_ms_window = state.rtt_ms_window if state else deque(maxlen=5000)
    if state:
        with state.lock:
            state.running = True
            state.finished = False
            state.stopped = False
            state.stop_requested = False
            state.last_error = ""
            state.task_id = contract.task_id
            state.receiver_url = contract.peers.get("receiver_url", "")
            state.video_path = video_path
            state.target_latency_ms = contract.app_config.get("target_latency_ms")
            state.infer_model_name = str(contract.app_config["infer_model_name"])
            state.fps = float(contract.app_config["fps"])
            state.width = int(contract.app_config["width"])
            state.height = int(contract.app_config["height"])
            state.contract = contract
            state.sent_frames = 0
            state.success_frames = 0
            state.error_frames = 0
            state.rtt_ms_window.clear()

    try:
        while True:
            if state:
                with state.lock:
                    if state.stop_requested:
                        state.stopped = True
                        state.last_error = "用户手动中止发送"
                        break
            start_loop = time.time()
            ok, frame = cap.read()
            if not ok:
                break

            encoded = encode_frame_jpeg(frame, int(contract.app_config["width"]), int(contract.app_config["height"]))
            if encoded is None:
                continue

            sent_ts_ns = time.time_ns()
            files = {"frame": ("frame.jpg", encoded, "image/jpeg")}
            data = {
                "task_id": contract.task_id,
                "frame_id": frame_id,
                "sent_ts_ns": sent_ts_ns,
                "fps": contract.app_config["fps"],
                "width": int(contract.app_config["width"]),
                "height": int(contract.app_config["height"]),
                "infer_model_name": contract.app_config["infer_model_name"],
            }
            if contract.app_config.get("target_latency_ms") is not None:
                data["target_latency_ms"] = contract.app_config["target_latency_ms"]

            try:
                req_start_ns = time.perf_counter_ns()
                resp = requests.post(f"{contract.peers['receiver_url']}/infer_frame", files=files, data=data, timeout=10)
                req_end_ns = time.perf_counter_ns()
                resp.raise_for_status()
                infer_ms = float(resp.json().get("infer_ms", -1))
                e2e_rtt_ms = (req_end_ns - req_start_ns) / 1_000_000.0
                rtt_ms_window.append(e2e_rtt_ms)
                if state:
                    with state.lock:
                        state.success_frames += 1
                        state.latest_rtt_ms = e2e_rtt_ms
                        state.latest_infer_ms = infer_ms if infer_ms >= 0 else None
                try:
                    requests.post(
                        f"{contract.peers['receiver_url']}/report_rtt",
                        data={"task_id": contract.task_id, "frame_id": frame_id, "rtt_ms": e2e_rtt_ms},
                        timeout=3,
                    )
                except Exception:
                    pass
                if reporter:
                    reporter.report(
                        contract.task_id,
                        contract.node_name,
                        "frame_sent_ack",
                        frame_id=frame_id,
                        latency_ms=e2e_rtt_ms,
                        payload={"infer_ms": infer_ms if infer_ms >= 0 else None},
                    )
            except Exception as exc:
                if state:
                    with state.lock:
                        state.error_frames += 1
                        state.last_error = str(exc)
                if reporter:
                    reporter.report(
                        contract.task_id,
                        contract.node_name,
                        "frame_send_error",
                        frame_id=frame_id,
                        payload={"error": str(exc)},
                    )

            frame_id += 1
            if state:
                with state.lock:
                    state.sent_frames = frame_id
            sleep_s = frame_interval - (time.time() - start_loop)
            if sleep_s > 0:
                time.sleep(sleep_s)
    finally:
        cap.release()
        if state:
            with state.lock:
                state.finished = True
                state.running = False
                state.worker = None
        if reporter:
            reporter.report(
                contract.task_id,
                contract.node_name,
                "sender_stop",
                payload={"total_frames": frame_id, "task_meta": contract.task_meta, "stopped": bool(state.stopped) if state else False},
            )


@sender_app.get("/metrics")
def sender_metrics():
    if sender_state is None:
        return JSONResponse({"ok": False, "error": "sender not initialized"}, status_code=500)
    with sender_state.lock:
        rtts = list(sender_state.rtt_ms_window)
        snapshot = {
            "task_id": sender_state.task_id,
            "video_path": sender_state.video_path,
            "receiver_url": sender_state.receiver_url,
            "fps": sender_state.fps,
            "width": sender_state.width,
            "height": sender_state.height,
            "target_latency_ms": sender_state.target_latency_ms,
            "infer_model_name": sender_state.infer_model_name,
            "default_video_path": sender_state.default_video_path,
            "available_videos": _list_videos(sender_state.default_video_path),
            "sent_frames": sender_state.sent_frames,
            "success_frames": sender_state.success_frames,
            "error_frames": sender_state.error_frames,
            "latest_rtt_ms": sender_state.latest_rtt_ms,
            "latest_infer_ms": sender_state.latest_infer_ms,
            "rtt_avg_ms": avg(rtts),
            "rtt_p95_ms": p95(rtts),
            "finished": sender_state.finished,
            "stopped": sender_state.stopped,
            "running": sender_state.running,
            "last_error": sender_state.last_error,
            "ui_policy": sender_state.contract.ui_policy,
        }
    _maybe_report_sender_ui_metrics(snapshot)
    return snapshot


@sender_app.get("/")
def sender_index():
    return serve_spa(os.getenv("WEB_DIR", "/web"), "index.html", ("metrics", "start_send", "stop_send"))


@sender_app.get("/{path:path}", include_in_schema=False)
def sender_spa(path: str):
    return serve_spa(os.getenv("WEB_DIR", "/web"), path, ("metrics", "start_send", "stop_send"))


@sender_app.post("/start_send")
async def start_send(
    task_id: str = Form(""),
    fps: float = Form(0),
    width: int = Form(0),
    height: int = Form(0),
    infer_model_name: str = Form(""),
    video_choice: str = Form("real"),
    video: UploadFile | None = File(default=None),
):
    if sender_state is None:
        return JSONResponse({"ok": False, "error": "sender not initialized"}, status_code=500)
    with sender_state.lock:
        if sender_state.running:
            return JSONResponse({"ok": False, "error": "sender is running"}, status_code=409)

    selected_task_id = task_id.strip() or sender_state.task_id
    selected_video_path = sender_state.default_video_path
    if video is not None and video.filename:
        target = sender_state.upload_dir / f"{int(time.time())}_{video.filename}"
        target.write_bytes(await video.read())
        selected_video_path = str(target)
    else:
        choice = (video_choice or "real").strip().lower()
        if choice == "auto":
            auto_path = _auto_video_path(sender_state.default_video_path)
            if not os.path.isfile(auto_path):
                return JSONResponse({"ok": False, "error": f"合成视频不存在：{auto_path}"}, status_code=400)
            selected_video_path = auto_path
        else:
            selected_video_path = sender_state.default_video_path

    cfg = parse_sender_args()
    cfg.task_id = selected_task_id
    cfg.video_path = selected_video_path
    if fps > 0:
        cfg.fps = float(fps)
    if width > 0:
        cfg.width = int(width)
    if height > 0:
        cfg.height = int(height)
    if infer_model_name.strip() in SUPPORTED_MODEL_ALIASES:
        cfg.infer_model_name = infer_model_name.strip()

    contract = build_sender_contract(cfg)
    sender_state.contract = contract
    worker = Thread(target=send_frames, args=(contract, sender_state), daemon=True)
    sender_state.worker = worker
    worker.start()
    return {"ok": True, "task_id": selected_task_id, "video_path": selected_video_path}


@sender_app.post("/stop_send")
def stop_send():
    if sender_state is None:
        return JSONResponse({"ok": False, "error": "sender not initialized"}, status_code=500)
    with sender_state.lock:
        if not sender_state.running:
            return JSONResponse({"ok": False, "error": "sender is not running"}, status_code=409)
        sender_state.stop_requested = True
    return {"ok": True}


def main():
    cfg = parse_sender_args()
    global sender_state
    sender_state = SenderState(cfg)
    if cfg.ui_port <= 0:
        send_frames(sender_state.contract, sender_state)
        return
    uvicorn.run(sender_app, host=cfg.ui_host, port=cfg.ui_port)


if __name__ == "__main__":
    main()

