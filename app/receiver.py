import asyncio
import json
import time
from collections import deque
from threading import Lock
from typing import Any

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app.common.config import ReceiverConfig, parse_receiver_args
from app.common.db import RuntimeReporter
from app.common.infer import BaseDetector, BoxDetector, RandomBoxDetector, build_detector

MODEL_ALIAS_TO_WEIGHT = {
    "yolov8": "yolov8n.pt",
    "yolov9": "yolov9c.pt",
}
SUPPORTED_MODEL_ALIASES = list(MODEL_ALIAS_TO_WEIGHT.keys())


class ReceiverState:
    def __init__(self):
        self.latest_jpeg = None
        self.latest_frame_id = -1
        self.latest_task_id = None
        self.task_stats: dict[str, dict[str, Any]] = {}
        self.lock = Lock()

    def _ensure_task(self, task_id: str) -> dict[str, Any]:
        if task_id not in self.task_stats:
            self.task_stats[task_id] = {
                "infer_ms_window": deque(maxlen=5000),
                "rtt_ms_window": deque(maxlen=5000),
                "infer_ms_series": deque(maxlen=60),
                "rtt_ms_series": deque(maxlen=60),
                "latest_rtt_ms": None,
                "latest_frame_id": -1,
                "profile": {},
            }
        return self.task_stats[task_id]


app = FastAPI(title="Video Receiver Inference Node")
state = ReceiverState()
runtime_reporter: RuntimeReporter | None = None
receiver_config: ReceiverConfig | None = None
detector: BaseDetector = BoxDetector()
model_detectors: dict[str, BaseDetector] = {}


@app.on_event("startup")
def startup_event() -> None:
    assert receiver_config is not None
    global runtime_reporter, detector
    if receiver_config.report_enabled:
        runtime_reporter = RuntimeReporter(
            receiver_config.db_url,
            redis_url=receiver_config.redis_url,
            redis_stream_key=receiver_config.redis_stream_key,
        )
    detector_backend = receiver_config.infer_backend
    try:
        detector = build_detector(
            receiver_config.infer_backend,
            receiver_config.yolo_model,
            receiver_config.yolo_conf,
        )
        detector_backend = detector.backend_name
    except Exception as e:
        detector = RandomBoxDetector()
        detector_backend = "random_box"
        if runtime_reporter:
            runtime_reporter.report(
                receiver_config.task_id,
                receiver_config.node_name,
                "detector_fallback",
                payload={"reason": str(e)},
            )
    if runtime_reporter:
        runtime_reporter.report(
            receiver_config.task_id,
            receiver_config.node_name,
            "receiver_start",
            payload={
                "task_meta": receiver_config.task_meta,
                "infer_backend": detector_backend,
                "yolo_model": receiver_config.yolo_model,
                "yolo_conf": receiver_config.yolo_conf,
            },
        )


@app.on_event("shutdown")
def shutdown_event() -> None:
    if runtime_reporter and receiver_config:
        runtime_reporter.report(
            receiver_config.task_id,
            receiver_config.node_name,
            "receiver_stop",
            payload={"task_meta": receiver_config.task_meta},
        )


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
    np_arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if img is None:
        return JSONResponse({"ok": False, "error": "decode failed"}, status_code=400)

    chosen_model_alias = infer_model_name.strip() or (receiver_config.yolo_model if receiver_config else "yolov8")
    if chosen_model_alias not in SUPPORTED_MODEL_ALIASES:
        chosen_model_alias = "yolov8"
    chosen_weight = MODEL_ALIAS_TO_WEIGHT[chosen_model_alias]
    chosen_detector = detector
    if receiver_config and receiver_config.infer_backend == "yolo":
        if chosen_weight not in model_detectors:
            try:
                model_detectors[chosen_weight] = build_detector("yolo", chosen_weight, receiver_config.yolo_conf)
            except Exception:
                model_detectors[chosen_weight] = RandomBoxDetector()
        chosen_detector = model_detectors[chosen_weight]

    infer_start_ns = time.perf_counter_ns()
    det = chosen_detector.detect_and_draw(img)
    img = det.image

    infer_end_ns = time.perf_counter_ns()
    infer_ms = (infer_end_ns - infer_start_ns) / 1_000_000.0
    cv2.putText(
        img,
        f"task={task_id} frame={frame_id} infer={infer_ms:.2f}ms backend={det.backend} boxes={det.box_count}",
        (20, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 0, 0),
        2,
    )

    ok, out = cv2.imencode(".jpg", img)
    if not ok:
        return JSONResponse({"ok": False, "error": "encode failed"}, status_code=500)

    with state.lock:
        stats = state._ensure_task(task_id)
        state.latest_jpeg = out.tobytes()
        state.latest_frame_id = frame_id
        state.latest_task_id = task_id
        stats["latest_frame_id"] = frame_id
        if fps > 0:
            stats["profile"]["fps"] = fps
        if width > 0 and height > 0:
            stats["profile"]["width"] = int(width)
            stats["profile"]["height"] = int(height)
        if target_latency_ms > 0:
            stats["profile"]["target_latency_ms"] = float(target_latency_ms)
        stats["profile"]["infer_model_name"] = chosen_model_alias
        stats["infer_ms_window"].append(infer_ms)
        stats["infer_ms_series"].append(infer_ms)

    if runtime_reporter:
        runtime_reporter.report(
            task_id=task_id,
            node_name=receiver_config.node_name if receiver_config else "receiver",
            event_type="frame_inferred",
            frame_id=frame_id,
            latency_ms=infer_ms,
            payload={"infer_backend": det.backend, "box_count": det.box_count},
        )

    return {"ok": True, "frame_id": frame_id, "infer_ms": infer_ms}


@app.post("/report_rtt")
def report_rtt(task_id: str = Form(...), frame_id: int = Form(...), rtt_ms: float = Form(...)):
    with state.lock:
        stats = state._ensure_task(task_id)
        stats["latest_frame_id"] = max(stats["latest_frame_id"], int(frame_id))
        stats["rtt_ms_window"].append(float(rtt_ms))
        stats["latest_rtt_ms"] = float(rtt_ms)
        stats["rtt_ms_series"].append(float(rtt_ms))
    if runtime_reporter:
        runtime_reporter.report(
            task_id=task_id,
            node_name=receiver_config.node_name if receiver_config else "receiver",
            event_type="frame_rtt",
            frame_id=frame_id,
            latency_ms=float(rtt_ms),
        )
    return {"ok": True}


@app.get("/metrics")
def metrics(task_id: str | None = Query(default=None)):
    with state.lock:
        target_task_id = task_id or state.latest_task_id or (receiver_config.task_id if receiver_config else None)
        if not target_task_id:
            return {"count": 0, "task_ids": list(state.task_stats.keys())}
        if target_task_id not in state.task_stats:
            return {"count": 0, "task_id": target_task_id, "task_ids": list(state.task_stats.keys())}
        stats = state.task_stats[target_task_id]
        infer_cnt = len(stats["infer_ms_window"])
        rtt_cnt = len(stats["rtt_ms_window"])
        if infer_cnt == 0 and rtt_cnt == 0:
            return {"count": 0, "task_id": target_task_id, "task_ids": list(state.task_stats.keys())}
        infer_avg = float(sum(stats["infer_ms_window"]) / infer_cnt) if infer_cnt > 0 else None
        infer_p95 = float(sorted(stats["infer_ms_window"])[int(infer_cnt * 0.95) - 1]) if infer_cnt > 0 else None
        rtt_avg = float(sum(stats["rtt_ms_window"]) / rtt_cnt) if rtt_cnt > 0 else None
        rtt_p95 = float(sorted(stats["rtt_ms_window"])[int(rtt_cnt * 0.95) - 1]) if rtt_cnt > 0 else None
        return {
            "task_id": target_task_id,
            "task_ids": list(state.task_stats.keys()),
            "count": max(infer_cnt, rtt_cnt),
            "latest_frame_id": stats["latest_frame_id"],
            "infer_avg_ms": infer_avg,
            "infer_p95_ms": infer_p95,
            "rtt_avg_ms": rtt_avg,
            "rtt_p95_ms": rtt_p95,
            "latest_rtt_ms": stats["latest_rtt_ms"],
            "infer_series_ms": list(stats["infer_ms_series"]),
            "rtt_series_ms": list(stats["rtt_ms_series"]),
            "profile": stats.get("profile", {}),
            "infer_backend": receiver_config.infer_backend if receiver_config else None,
            "infer_model_name": receiver_config.yolo_model if receiver_config else None,
            "receiver_target_latency_ms": receiver_config.target_latency_ms if receiver_config else None,
            "supported_models": SUPPORTED_MODEL_ALIASES,
        }


@app.get("/metrics_sse")
async def metrics_sse(task_id: str | None = Query(default=None)):
    async def event_stream():
        while True:
            payload = metrics(task_id)
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
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
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )

    return StreamingResponse(generator(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/", response_class=HTMLResponse)
def index():
    task_id = receiver_config.task_id if receiver_config else "unknown"
    html = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>接收节点看板</title>
  <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
  <style>
    body { font-family: Arial, sans-serif; margin: 16px; }
    .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }
    .cards { display:flex; gap:10px; flex-wrap:wrap; margin:12px 0; }
    .card { border:1px solid #ddd; border-radius:8px; padding:10px 12px; min-width:180px; background:#fff; }
    .k { color:#666; font-size:12px; }
    .v { font-size:20px; font-weight:600; margin-top:4px; }
    .video-wrap { width:min(95vw,960px); min-height:360px; border:1px solid #ccc; border-radius:8px; background:#f7f7f7; display:flex; align-items:center; justify-content:center; overflow:hidden; }
    .video-wrap img { width:100%; }
    pre { background:#f8f8f8; padding:8px; border-radius:6px; }
  </style>
</head>
<body>
<div id="app">
  <h2>接收节点看板（Receiver）</h2>
  <div class="toolbar">
    <span>任务ID（task_id）</span>
    <select v-model="selectedTaskId" @change="reconnectSSE" style="padding:4px 8px;min-width:320px;">
      <option v-for="t in taskIds" :key="t" :value="t">{{ t }}</option>
    </select>
    <span>{{ current.task_id || '-' }}</span>
    <span :style="{color: liveOk ? '#2d7' : '#d55', fontSize:'12px'}">{{ liveText }}</span>
  </div>

  <div class="video-wrap">
    <img v-if="hasFrame" :src="streamUrl" alt="stream">
    <div v-else style="color:#777;font-size:14px;">暂无视频帧，等待 sender 发送...</div>
  </div>

  <div class="cards">
    <div class="card"><div class="k">帧数（Frames）</div><div class="v">{{ current.count || 0 }}</div></div>
    <div class="card"><div class="k">推理均值（Infer AVG, ms）</div><div class="v">{{ fmt(current.infer_avg_ms) }}</div></div>
    <div class="card"><div class="k">推理P95（Infer P95, ms）</div><div class="v">{{ fmt(current.infer_p95_ms) }}</div></div>
    <div class="card"><div class="k">时延均值（RTT AVG, ms）</div><div class="v">{{ fmt(current.rtt_avg_ms) }}</div></div>
    <div class="card"><div class="k">时延P95（RTT P95, ms）</div><div class="v">{{ fmt(current.rtt_p95_ms) }}</div></div>
    <div class="card"><div class="k">最新时延（Latest RTT, ms）</div><div class="v">{{ fmt(current.latest_rtt_ms) }}</div></div>
  </div>

  <pre>{{ metaText }}</pre>
  <canvas id="trend" width="960" height="280" style="max-width:95vw;border:1px solid #ddd;border-radius:8px;margin-bottom:10px;"></canvas>
  <pre>{{ JSON.stringify(current, null, 2) }}</pre>
</div>
<script>
const { createApp } = Vue;
createApp({
  data() {
    return {
      selectedTaskId: "__TASK_ID__",
      taskIds: [],
      current: {},
      es: null,
      liveOk: false,
      liveText: '实时连接中...',
      streamSeed: Date.now(),
    };
  },
  computed: {
    hasFrame() { return (this.current.latest_frame_id ?? -1) >= 0 && (this.current.count ?? 0) > 0; },
    streamUrl() { return '/stream.mjpg?_=' + this.streamSeed; },
    metaText() {
      const p = this.current.profile || {};
      const res = (p.width && p.height) ? `${p.width}x${p.height}` : '-';
      return [
        `任务ID(task_id)=${this.current.task_id || '-'}`,
        `帧率(FPS)=${p.fps ?? '-'} 分辨率(Resolution)=${res}`,
        `模型(Model)=${this.current.infer_model_name || '-'} 后端(Backend)=${this.current.infer_backend || '-'}`,
        `目标时延(Target Latency, ms)=${p.target_latency_ms ?? this.current.receiver_target_latency_ms ?? '-'}`
      ].join('\\n');
    }
  },
  methods: {
    fmt(v) { return (v === null || v === undefined) ? '-' : Number(v).toFixed(2); },
    drawTrend(inferSeries, rttSeries) {
      const c = document.getElementById('trend'); if (!c) return;
      const ctx = c.getContext('2d'); const w = c.width; const h = c.height;
      ctx.clearRect(0, 0, w, h); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
      const padL=48,padR=20,padT=20,padB=30, chartW=w-padL-padR, chartH=h-padT-padB;
      const all=[...(inferSeries||[]),...(rttSeries||[])].filter(v=>v!=null);
      const maxV=all.length?Math.max(...all):10; const yMax=Math.max(maxV*1.1,10);
      ctx.strokeStyle='#eee'; ctx.lineWidth=1;
      for(let i=0;i<=4;i++){ const y=padT+(chartH*i)/4; ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(w-padR,y); ctx.stroke(); ctx.fillStyle='#666'; ctx.font='11px Arial'; ctx.fillText((yMax*(1-i/4)).toFixed(1),8,y+4); }
      const draw=(s,color)=>{ if(!s||s.length<2) return; ctx.strokeStyle=color; ctx.lineWidth=2; ctx.beginPath(); s.forEach((v,i)=>{ const x=padL+(chartW*i)/Math.max(s.length-1,1); const y=padT+chartH*(1-Math.min(v,yMax)/yMax); if(i===0)ctx.moveTo(x,y); else ctx.lineTo(x,y);}); ctx.stroke(); };
      draw(inferSeries,'#2f80ed'); draw(rttSeries,'#eb5757');
    },
    applyMetrics(j) {
      this.current = j || {};
      this.taskIds = j.task_ids || [];
      if (j.task_id) this.selectedTaskId = j.task_id;
      this.drawTrend(j.infer_series_ms || [], j.rtt_series_ms || []);
      if (this.hasFrame) this.streamSeed = Date.now();
    },
    reconnectSSE() {
      if (this.es) this.es.close();
      const q = this.selectedTaskId ? ('?task_id=' + encodeURIComponent(this.selectedTaskId)) : '';
      this.es = new EventSource('/metrics_sse' + q);
      this.es.onopen = () => { this.liveOk = true; this.liveText = '实时连接已建立'; };
      this.es.onerror = () => { this.liveOk = false; this.liveText = '实时连接重试中...'; };
      this.es.onmessage = (evt) => { try { this.applyMetrics(JSON.parse(evt.data)); } catch (e) {} };
    },
    async initData() {
      try {
        const r = await fetch('/metrics?_=' + Date.now(), { cache: 'no-store' });
        this.applyMetrics(await r.json());
      } catch (e) {}
      this.reconnectSSE();
    }
  },
  mounted() { this.initData(); },
  beforeUnmount() { if (this.es) this.es.close(); }
}).mount('#app');
</script>
</body>
</html>
"""
    return html.replace("__TASK_ID__", task_id)


def main():
    global receiver_config
    receiver_config = parse_receiver_args()
    uvicorn.run(app, host=receiver_config.host, port=receiver_config.port)


if __name__ == "__main__":
    main()
