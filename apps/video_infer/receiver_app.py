from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from threading import Lock
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.common.config import ReceiverConfig, parse_receiver_args
from app.common.infer import BaseDetector, BoxDetector, RandomBoxDetector, build_detector
from apps.video_infer.core.metrics import avg, p95
from runtime.net import run_uvicorn
from runtime.report_client import ReportClient
from runtime.task_contract import RuntimeTaskContract, build_receiver_contract
from runtime.web_static import serve_spa

MODEL_ALIAS_TO_WEIGHT = {"yolov8": "yolov8n.pt", "yolov9": "yolov9c.pt"}
SUPPORTED_MODEL_ALIASES = list(MODEL_ALIAS_TO_WEIGHT.keys())


class ReceiverState:
    def __init__(self):
        self.latest_jpeg = None
        self.latest_frame_id = -1
        self.active_task_id = None
        self.active_stats: dict[str, Any] | None = None
        self.last_frame_at = 0.0
        self.lock = Lock()

    def reset_task(self, task_id: str) -> dict[str, Any]:
        self.active_task_id = task_id
        self.latest_frame_id = -1
        self.latest_jpeg = None
        self.active_stats = {
            "infer_ms_window": deque(maxlen=5000),
            "rtt_ms_window": deque(maxlen=5000),
            "infer_ms_series": deque(maxlen=60),
            "rtt_ms_series": deque(maxlen=60),
            "latest_rtt_ms": None,
            "latest_frame_id": -1,
            "rtt_total": 0,
            "rtt_meet_target": 0,
            "profile": {},
        }
        return self.active_stats

    def get_active_task(self) -> tuple[str | None, dict[str, Any] | None]:
        return self.active_task_id, self.active_stats


app = FastAPI(title="Video Receiver Inference Node")
cors_origins = [x.strip() for x in os.getenv("FRONTEND_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
state = ReceiverState()
runtime_reporter: ReportClient | None = None
receiver_config: ReceiverConfig | None = None
receiver_contract: RuntimeTaskContract | None = None
detector: BaseDetector = BoxDetector()
model_detectors: dict[str, BaseDetector] = {}
_ui_last_report_ts: dict[str, float] = {}
STREAM_IDLE_RESET_SECONDS = 3.0


def _idle_threshold_seconds(profile_fps: float) -> float:
    if profile_fps and profile_fps > 0:
        return max(STREAM_IDLE_RESET_SECONDS, 3.0 / float(profile_fps))
    return STREAM_IDLE_RESET_SECONDS


def _build_stream_profile(
    fps: float,
    width: int,
    height: int,
    target_latency_ms: float,
    sent_ts_ns: int,
    infer_model_name: str,
) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    if fps > 0:
        profile["fps"] = fps
    if width > 0 and height > 0:
        profile["width"] = int(width)
        profile["height"] = int(height)
    if target_latency_ms > 0:
        profile["target_latency_ms"] = float(target_latency_ms)
    if sent_ts_ns > 0:
        profile["sent_ts_ns"] = sent_ts_ns
    profile["infer_model_name"] = infer_model_name
    return profile


def _should_reset_active_task(stats: dict[str, Any], frame_id: int, incoming_profile: dict[str, Any]) -> bool:
    prev = int(stats.get("latest_frame_id", -1))
    if frame_id < prev:
        return True
    if prev >= 0 and frame_id == 0:
        return True
    if prev < 0:
        return False
    current_profile = stats.get("profile", {})
    for key in ("fps", "width", "height", "target_latency_ms", "infer_model_name"):
        if incoming_profile.get(key) != current_profile.get(key):
            return True
    return False


def _maybe_report_ui_metrics(snapshot: dict[str, Any], source: str) -> None:
    """
    Report UI-visible aggregated metrics snapshot to Redis/DB via runtime_reporter.
    Rate-limited per task_id to avoid flooding (SSE/polling).
    """
    if runtime_reporter is None or receiver_contract is None:
        return
    task_id = str(snapshot.get("task_id") or receiver_contract.task_id or "")
    if not task_id:
        return
    now = time.time()
    last = _ui_last_report_ts.get(task_id, 0.0)
    if now - last < 5.0:
        return
    _ui_last_report_ts[task_id] = now
    payload = {
        "role": "receiver",
        "source": source,
        "count": snapshot.get("count"),
        "latest_frame_id": snapshot.get("latest_frame_id"),
        "infer_avg_ms": snapshot.get("infer_avg_ms"),
        "infer_p95_ms": snapshot.get("infer_p95_ms"),
        "rtt_avg_ms": snapshot.get("rtt_avg_ms"),
        "rtt_p95_ms": snapshot.get("rtt_p95_ms"),
        "latest_rtt_ms": snapshot.get("latest_rtt_ms"),
        "profile": snapshot.get("profile", {}),
        "infer_backend": snapshot.get("infer_backend"),
        "supported_models": snapshot.get("supported_models"),
    }
    runtime_reporter.report(task_id, receiver_contract.node_name, "ui_metrics", payload=payload)


@app.on_event("startup")
def startup_event() -> None:
    global runtime_reporter, detector
    assert receiver_contract is not None
    if receiver_contract.reporting.enabled:
        runtime_reporter = ReportClient(
            receiver_contract.reporting.db_url,
            redis_url=receiver_contract.reporting.redis_url,
            redis_stream_key=receiver_contract.reporting.redis_stream_key,
        )
    detector_backend = str(receiver_contract.app_config.get("infer_backend", "yolo"))
    yolo_model_raw = str(receiver_contract.app_config.get("yolo_model", "yolov8")).strip()
    yolo_weight = MODEL_ALIAS_TO_WEIGHT.get(yolo_model_raw, yolo_model_raw)
    try:
        detector = build_detector(
            detector_backend,
            yolo_weight,
            float(receiver_contract.app_config.get("yolo_conf", 0.25)),
        )
        detector_backend = detector.backend_name
    except Exception as exc:
        detector = RandomBoxDetector()
        detector_backend = "random_box"
        if runtime_reporter:
            runtime_reporter.report(receiver_contract.task_id, receiver_contract.node_name, "detector_fallback", payload={"reason": str(exc)})
    if runtime_reporter:
        runtime_reporter.report(
            receiver_contract.task_id,
            receiver_contract.node_name,
            "receiver_start",
            payload={
                "task_meta": receiver_contract.task_meta,
                "infer_backend": detector_backend,
                "yolo_model": receiver_contract.app_config.get("yolo_model"),
                "yolo_conf": receiver_contract.app_config.get("yolo_conf"),
            },
        )


@app.on_event("shutdown")
def shutdown_event() -> None:
    if runtime_reporter and receiver_contract:
        runtime_reporter.report(receiver_contract.task_id, receiver_contract.node_name, "receiver_stop", payload={"task_meta": receiver_contract.task_meta})


@app.post("/infer_frame")
async def infer_frame(
    task_id: str = Form(...),
    frame_id: int = Form(...),
    sent_ts_ns: int = Form(0),
    fps: float = Form(0),
    width: int = Form(0),
    height: int = Form(0),
    target_latency_ms: float = Form(0),
    infer_model_name: str = Form(""),
    frame: UploadFile = File(...),
):
    image_bytes = await frame.read()
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return JSONResponse({"ok": False, "error": "decode failed"}, status_code=400)

    chosen_model_alias = infer_model_name.strip() or (receiver_contract.app_config.get("yolo_model", "yolov8") if receiver_contract else "yolov8")
    if chosen_model_alias not in SUPPORTED_MODEL_ALIASES:
        chosen_model_alias = "yolov8"
    chosen_weight = MODEL_ALIAS_TO_WEIGHT[chosen_model_alias]
    chosen_detector = detector
    if receiver_contract and receiver_contract.app_config.get("infer_backend") == "yolo":
        if chosen_weight not in model_detectors:
            try:
                model_detectors[chosen_weight] = build_detector("yolo", chosen_weight, float(receiver_contract.app_config.get("yolo_conf", 0.25)))
            except Exception:
                model_detectors[chosen_weight] = RandomBoxDetector()
        chosen_detector = model_detectors[chosen_weight]

    incoming_profile = _build_stream_profile(fps, width, height, target_latency_ms, sent_ts_ns, chosen_model_alias)

    with state.lock:
        now = time.time()
        active_id, stats = state.get_active_task()

        if active_id is not None and stats is not None:
            prev_fps = float(stats.get("profile", {}).get("fps") or 0)
            if (now - state.last_frame_at) > _idle_threshold_seconds(prev_fps):
                state.active_task_id = None
                state.active_stats = None
                active_id, stats = None, None

        if active_id is None:
            stats = state.reset_task(task_id)
        elif active_id != task_id:
            return JSONResponse(
                {"ok": False, "error": "receiver_busy", "active_task_id": active_id},
                status_code=409,
            )
        else:
            if stats is None:
                stats = state.reset_task(task_id)
            elif _should_reset_active_task(stats, frame_id, incoming_profile):
                stats = state.reset_task(task_id)

        stats["profile"].update(incoming_profile)

        infer_start_ns = time.perf_counter_ns()
        det = chosen_detector.detect_and_draw(img)
        infer_ms = (time.perf_counter_ns() - infer_start_ns) / 1_000_000.0
        cv2.putText(
            det.image,
            f"task={task_id} frame={frame_id} infer={infer_ms:.2f}ms backend={det.backend} boxes={det.box_count}",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 0, 0),
            2,
        )
        ok, out = cv2.imencode(".jpg", det.image)
        if not ok:
            return JSONResponse({"ok": False, "error": "encode failed"}, status_code=500)

        state.latest_jpeg = out.tobytes()
        state.latest_frame_id = frame_id
        stats["latest_frame_id"] = frame_id
        stats["infer_ms_window"].append(infer_ms)
        stats["infer_ms_series"].append(infer_ms)
        state.last_frame_at = time.time()

    if runtime_reporter:
        runtime_reporter.report(task_id, receiver_contract.node_name if receiver_contract else "receiver", "frame_inferred", frame_id=frame_id, latency_ms=infer_ms, payload={"infer_backend": det.backend, "box_count": det.box_count})
    return {"ok": True, "frame_id": frame_id, "infer_ms": infer_ms}


@app.post("/report_rtt")
def report_rtt(task_id: str = Form(...), frame_id: int = Form(...), rtt_ms: float = Form(...)):
    with state.lock:
        if task_id != state.active_task_id or state.active_stats is None:
            return {"ok": True, "ignored": True}
        stats = state.active_stats
        stats["latest_frame_id"] = max(stats["latest_frame_id"], int(frame_id))
        stats["rtt_ms_window"].append(float(rtt_ms))
        stats["latest_rtt_ms"] = float(rtt_ms)
        stats["rtt_ms_series"].append(float(rtt_ms))
        stats["rtt_total"] = int(stats.get("rtt_total", 0)) + 1
        target = stats.get("profile", {}).get("target_latency_ms") or 0
        if target and float(rtt_ms) <= float(target):
            stats["rtt_meet_target"] = int(stats.get("rtt_meet_target", 0)) + 1
    if runtime_reporter:
        runtime_reporter.report(task_id, receiver_contract.node_name if receiver_contract else "receiver", "frame_rtt", frame_id=frame_id, latency_ms=float(rtt_ms))
    return {"ok": True}


@app.get("/metrics")
def metrics(task_id: str | None = Query(default=None)):
    # 单流模式：仅暴露当前活动 task；?task_id 保留兼容，不参与筛选。
    _ = task_id
    with state.lock:
        if state.active_task_id is None or state.active_stats is None:
            return {
                "count": 0,
                "task_id": None,
                "task_ids": [],
                "supported_models": SUPPORTED_MODEL_ALIASES,
                "infer_backend": receiver_contract.app_config.get("infer_backend") if receiver_contract else None,
                "infer_model_name": receiver_contract.app_config.get("yolo_model") if receiver_contract else None,
                "receiver_target_latency_ms": receiver_contract.app_config.get("target_latency_ms") if receiver_contract else None,
                "ui_policy": receiver_contract.ui_policy if receiver_contract else {},
            }
        target_task_id = state.active_task_id
        stats = state.active_stats
        infer_values = list(stats["infer_ms_window"])
        rtt_values = list(stats["rtt_ms_window"])
        if not infer_values and not rtt_values:
            return {
                "count": 0,
                "task_id": target_task_id,
                "task_ids": [],
                "profile": stats.get("profile", {}),
                "supported_models": SUPPORTED_MODEL_ALIASES,
                "infer_backend": receiver_contract.app_config.get("infer_backend") if receiver_contract else None,
                "infer_model_name": receiver_contract.app_config.get("yolo_model") if receiver_contract else None,
                "receiver_target_latency_ms": receiver_contract.app_config.get("target_latency_ms") if receiver_contract else None,
                "ui_policy": receiver_contract.ui_policy if receiver_contract else {},
            }
        snapshot = {
            "task_id": target_task_id,
            "task_ids": [],
            "count": max(len(infer_values), len(rtt_values)),
            "latest_frame_id": stats["latest_frame_id"],
            "infer_avg_ms": avg(infer_values),
            "infer_p95_ms": p95(infer_values),
            "rtt_avg_ms": avg(rtt_values),
            "rtt_p95_ms": p95(rtt_values),
            "latest_rtt_ms": stats["latest_rtt_ms"],
            "infer_series_ms": list(stats["infer_ms_series"]),
            "rtt_series_ms": list(stats["rtt_ms_series"]),
            "profile": stats.get("profile", {}),
            "rtt_total": int(stats.get("rtt_total", 0)),
            "rtt_meet_target": int(stats.get("rtt_meet_target", 0)),
            "rtt_meet_ratio": (float(stats.get("rtt_meet_target", 0)) / float(stats.get("rtt_total", 1))) if stats.get("rtt_total", 0) else None,
            "infer_backend": receiver_contract.app_config.get("infer_backend") if receiver_contract else None,
            "infer_model_name": receiver_contract.app_config.get("yolo_model") if receiver_contract else None,
            "receiver_target_latency_ms": receiver_contract.app_config.get("target_latency_ms") if receiver_contract else None,
            "supported_models": SUPPORTED_MODEL_ALIASES,
            "ui_policy": receiver_contract.ui_policy if receiver_contract else {},
        }
    _maybe_report_ui_metrics(snapshot, source="metrics")
    return snapshot


@app.get("/metrics_sse")
async def metrics_sse(task_id: str | None = Query(default=None)):
    async def event_stream():
        while True:
            snap = metrics(task_id)
            _maybe_report_ui_metrics(snap, source="metrics_sse")
            yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/stream.mjpg")
async def stream_mjpeg():
    async def generator():
        while True:
            await asyncio.sleep(0.05)
            with state.lock:
                frame_bytes = state.latest_jpeg
            if frame_bytes is None:
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"

    return StreamingResponse(generator(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/")
def index():
    return serve_spa(os.getenv("WEB_DIR", "/web"), "index.html", ("metrics", "metrics_sse", "infer_frame", "report_rtt", "stream.mjpg"))


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str):
    return serve_spa(os.getenv("WEB_DIR", "/web"), path, ("metrics", "metrics_sse", "infer_frame", "report_rtt", "stream.mjpg"))


def main():
    global receiver_config, receiver_contract
    receiver_config = parse_receiver_args()
    receiver_contract = build_receiver_contract(receiver_config)
    run_uvicorn(app, receiver_config.host, receiver_config.port)


if __name__ == "__main__":
    main()

