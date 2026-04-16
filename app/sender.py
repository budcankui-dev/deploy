import time
from collections import deque
from pathlib import Path
from threading import Lock, Thread

import cv2
import requests
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from app.common.config import SenderConfig, parse_sender_args
from app.common.db import RuntimeReporter

MODEL_ALIAS_TO_WEIGHT = {
    "yolov8": "yolov8n.pt",
    "yolov9": "yolov9c.pt",
}
SUPPORTED_MODEL_ALIASES = list(MODEL_ALIAS_TO_WEIGHT.keys())


class SenderState:
    def __init__(self, cfg: SenderConfig):
        self.task_id = cfg.task_id
        self.receiver_url = cfg.receiver_url
        self.fps = cfg.fps
        self.width = cfg.width
        self.height = cfg.height
        self.video_path = cfg.video_path
        self.default_video_path = cfg.video_path
        self.target_latency_ms = cfg.target_latency_ms
        self.infer_model_name = cfg.infer_model_name
        self.sent_frames = 0
        self.success_frames = 0
        self.error_frames = 0
        self.latest_rtt_ms = None
        self.latest_infer_ms = None
        self.rtt_ms_window = deque(maxlen=5000)
        self.started_at = time.time()
        self.finished = False
        self.last_error = ""
        self.running = False
        self.worker: Thread | None = None
        self.upload_dir = Path("./uploads")
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()


sender_app = FastAPI(title="Video Sender Node")
sender_state: SenderState | None = None


def send_frames(cfg: SenderConfig, state: SenderState | None = None) -> None:
    reporter = None
    if cfg.report_enabled:
        reporter = RuntimeReporter(cfg.db_url, redis_url=cfg.redis_url, redis_stream_key=cfg.redis_stream_key)
        reporter.report(cfg.task_id, cfg.node_name, "sender_start", payload={"task_meta": cfg.task_meta})

    cap = cv2.VideoCapture(cfg.video_path)
    if not cap.isOpened():
        if reporter:
            reporter.report(cfg.task_id, cfg.node_name, "sender_error", payload={"reason": "video_open_failed"})
        raise RuntimeError(f"无法打开视频: {cfg.video_path}")

    frame_interval = 1.0 / cfg.fps
    frame_id = 0
    rtt_ms_window = state.rtt_ms_window if state else deque(maxlen=5000)
    if state:
        with state.lock:
            state.running = True
            state.finished = False
            state.last_error = ""
            state.task_id = cfg.task_id
            state.video_path = cfg.video_path
            state.target_latency_ms = cfg.target_latency_ms
            state.infer_model_name = cfg.infer_model_name
            state.sent_frames = 0
            state.success_frames = 0
            state.error_frames = 0
            state.rtt_ms_window.clear()
    print(f"[sender] start task={cfg.task_id} receiver={cfg.receiver_url} fps={cfg.fps} size={cfg.width}x{cfg.height}")
    try:
        while True:
            start_loop = time.time()
            ok, frame = cap.read()
            if not ok:
                break

            frame = cv2.resize(frame, (cfg.width, cfg.height))
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                continue

            sent_ts_ns = time.time_ns()
            files = {"frame": ("frame.jpg", encoded.tobytes(), "image/jpeg")}
            data = {"task_id": cfg.task_id, "frame_id": frame_id, "sent_ts_ns": sent_ts_ns}
            data["fps"] = cfg.fps
            data["width"] = cfg.width
            data["height"] = cfg.height
            if cfg.target_latency_ms is not None:
                data["target_latency_ms"] = cfg.target_latency_ms

            try:
                req_start_ns = time.perf_counter_ns()
                resp = requests.post(
                    f"{cfg.receiver_url}/infer_frame",
                    files=files,
                    data=data,
                    timeout=10,
                )
                req_end_ns = time.perf_counter_ns()
                resp.raise_for_status()
                j = resp.json()
                infer_ms = float(j.get("infer_ms", -1))
                e2e_rtt_ms = (req_end_ns - req_start_ns) / 1_000_000.0
                rtt_ms_window.append(e2e_rtt_ms)
                if state:
                    with state.lock:
                        state.success_frames += 1
                        state.latest_rtt_ms = e2e_rtt_ms
                        state.latest_infer_ms = infer_ms if infer_ms >= 0 else None
                try:
                    requests.post(
                        f"{cfg.receiver_url}/report_rtt",
                        data={"task_id": cfg.task_id, "frame_id": frame_id, "rtt_ms": e2e_rtt_ms},
                        timeout=3,
                    )
                except Exception:
                    pass
                if reporter:
                    reporter.report(
                        cfg.task_id,
                        cfg.node_name,
                        "frame_sent_ack",
                        frame_id=frame_id,
                        latency_ms=e2e_rtt_ms,
                        payload={"infer_ms": infer_ms if infer_ms >= 0 else None},
                    )
            except Exception as e:
                if state:
                    with state.lock:
                        state.error_frames += 1
                        state.last_error = str(e)
                if reporter:
                    reporter.report(
                        cfg.task_id,
                        cfg.node_name,
                        "frame_send_error",
                        frame_id=frame_id,
                        payload={"error": str(e)},
                    )
                print(f"[sender][error] frame={frame_id} post failed: {e}")

            frame_id += 1
            if state:
                with state.lock:
                    state.sent_frames = frame_id
            if frame_id % 50 == 0:
                if rtt_ms_window:
                    vals = sorted(rtt_ms_window)
                    cnt = len(vals)
                    p95 = vals[max(int(cnt * 0.95) - 1, 0)]
                    avg = sum(vals) / cnt
                    print(f"[sender] sent_frames={frame_id} rtt_avg={avg:.2f}ms rtt_p95={p95:.2f}ms")
                else:
                    print(f"[sender] sent_frames={frame_id}")
            elapsed = time.time() - start_loop
            sleep_s = frame_interval - elapsed
            if sleep_s > 0:
                time.sleep(sleep_s)
    finally:
        cap.release()
        if state:
            with state.lock:
                state.finished = True
                state.running = False
        print(f"[sender] stop total_frames={frame_id}")
        if reporter:
            reporter.report(
                cfg.task_id,
                cfg.node_name,
                "sender_stop",
                payload={"total_frames": frame_id, "task_meta": cfg.task_meta},
            )


def main():
    cfg = parse_sender_args()
    global sender_state
    sender_state = SenderState(cfg)
    if cfg.ui_port <= 0:
        send_frames(cfg, sender_state)
        return

    uvicorn.run(sender_app, host=cfg.ui_host, port=cfg.ui_port)


@sender_app.get("/metrics")
def sender_metrics():
    if sender_state is None:
        return JSONResponse({"ok": False, "error": "sender not initialized"}, status_code=500)
    with sender_state.lock:
        cnt = len(sender_state.rtt_ms_window)
        rtt_avg = float(sum(sender_state.rtt_ms_window) / cnt) if cnt > 0 else None
        rtt_p95 = float(sorted(sender_state.rtt_ms_window)[int(cnt * 0.95) - 1]) if cnt > 0 else None
        return {
            "task_id": sender_state.task_id,
            "video_path": sender_state.video_path,
            "receiver_url": sender_state.receiver_url,
            "fps": sender_state.fps,
            "width": sender_state.width,
            "height": sender_state.height,
            "target_latency_ms": sender_state.target_latency_ms,
            "infer_model_name": sender_state.infer_model_name,
            "default_video_path": sender_state.default_video_path,
            "sent_frames": sender_state.sent_frames,
            "success_frames": sender_state.success_frames,
            "error_frames": sender_state.error_frames,
            "latest_rtt_ms": sender_state.latest_rtt_ms,
            "latest_infer_ms": sender_state.latest_infer_ms,
            "rtt_avg_ms": rtt_avg,
            "rtt_p95_ms": rtt_p95,
            "finished": sender_state.finished,
            "running": sender_state.running,
            "last_error": sender_state.last_error,
        }


@sender_app.get("/", response_class=HTMLResponse)
def sender_index():
    return """
<!doctype html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>Sender 节点看板</title>
<script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<style>
body { font-family: Arial, sans-serif; margin: 16px; }
.cards { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 10px 12px; min-width: 160px; }
.k { color: #666; font-size: 12px; }
.v { font-size: 20px; font-weight: 600; margin-top: 4px; }
#meta, #raw { background: #f8f8f8; padding: 8px; border-radius: 6px; }
</style>
</head>
<body>
<div id="app">
  <h2>Sender 节点看板</h2>
  <pre id="meta">{{ metaText }}</pre>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;align-items:end;margin:8px 0;">
    <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:#444;">任务ID（task_id）
      <input v-model="task_id" style="padding:6px;background:#f5f5f5;" readonly />
    </label>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:#444;">接收地址（receiver_url）
      <input v-model="receiver_url" style="padding:6px;background:#f5f5f5;" readonly />
    </label>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:#444;">帧率（FPS）
      <input v-model="fps" type="number" step="0.1" min="0.1" style="padding:6px;" />
    </label>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:#444;">宽度（px）
      <input v-model="width" type="number" min="1" style="padding:6px;" />
    </label>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:#444;">高度（px）
      <input v-model="height" type="number" min="1" style="padding:6px;" />
    </label>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:#444;">目标时延（ms，任务分配只读）
      <input v-model="target_latency_ms" type="number" min="0" step="0.1" style="padding:6px;background:#f5f5f5;" readonly />
    </label>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:#444;">模型（Model）
      <select v-model="infer_model_name" style="padding:6px;">
      <option value="yolov8">YOLOv8</option>
      <option value="yolov9">YOLOv9</option>
      </select>
    </label>
    <label style="display:flex;flex-direction:column;gap:4px;font-size:12px;color:#444;">视频文件（可选）
      <input @change="onFileChange" type="file" accept="video/*" />
    </label>
    <div style="display:flex;align-items:center;gap:8px;">
      <button :disabled="running" @click="startSend" style="padding:8px 12px;">{{ running ? '发送中...' : '开始发送' }}</button>
      <span style="color:#666;font-size:12px;">未选择文件时默认使用 `test.mp4`</span>
    </div>
  </div>
  <div style="margin:4px 0 10px 0;color:#555;">{{ hint }}</div>
  <div class="cards">
    <div class="card"><div class="k">已发送帧（Sent Frames）</div><div class="v">{{ sent_frames || 0 }}</div></div>
    <div class="card"><div class="k">成功帧（Success）</div><div class="v">{{ success_frames || 0 }}</div></div>
    <div class="card"><div class="k">失败帧（Errors）</div><div class="v">{{ error_frames || 0 }}</div></div>
    <div class="card"><div class="k">平均时延（RTT AVG, ms）</div><div class="v">{{ fmt(rtt_avg_ms) }}</div></div>
    <div class="card"><div class="k">P95时延（RTT P95, ms）</div><div class="v">{{ fmt(rtt_p95_ms) }}</div></div>
  </div>
  <pre id="raw">{{ JSON.stringify(raw, null, 2) }}</pre>
</div>
<script>
const { createApp } = Vue;
createApp({
  data() {
    return { raw: {}, file: null, hint: '未选择视频时，默认使用 test.mp4（或启动参数中的 --video-path）。' };
  },
  computed: {
    metaText() {
      const j = this.raw || {};
      return `任务ID(task_id)=${j.task_id || ''}\n接收地址(receiver_url)=${j.receiver_url || ''}\n默认视频(default_video)=${j.default_video_path || ''}\n当前视频(current_video)=${j.video_path || ''}\n分辨率(resolution)=${j.width || '-'}x${j.height || '-'} 帧率(FPS)=${j.fps || '-'}\n模型(model)=${j.infer_model_name || '-'}\n目标时延(target_latency_ms)=${j.target_latency_ms ?? '-'}\n运行中(running)=${j.running} 已结束(finished)=${j.finished}`;
    },
    task_id: { get() { return this.raw.task_id || ''; }, set() {} },
    receiver_url: { get() { return this.raw.receiver_url || ''; }, set() {} },
    fps: { get() { return this.raw.fps || ''; }, set(v) { this.raw.fps = Number(v); } },
    width: { get() { return this.raw.width || ''; }, set(v) { this.raw.width = Number(v); } },
    height: { get() { return this.raw.height || ''; }, set(v) { this.raw.height = Number(v); } },
    target_latency_ms: { get() { return this.raw.target_latency_ms ?? ''; }, set() {} },
    infer_model_name: { get() { return this.raw.infer_model_name || 'yolov8'; }, set(v) { this.raw.infer_model_name = v; } },
    sent_frames() { return this.raw.sent_frames; },
    success_frames() { return this.raw.success_frames; },
    error_frames() { return this.raw.error_frames; },
    rtt_avg_ms() { return this.raw.rtt_avg_ms; },
    rtt_p95_ms() { return this.raw.rtt_p95_ms; },
    running() { return !!this.raw.running; },
  },
  methods: {
    fmt(v) { return (v === null || v === undefined) ? '-' : Number(v).toFixed(2); },
    onFileChange(e) { this.file = e.target.files?.[0] || null; },
    async refresh() {
      try {
        const r = await fetch('/metrics?_=' + Date.now(), { cache: 'no-store' });
        this.raw = await r.json();
      } catch (e) {}
    },
    async startSend() {
      const fd = new FormData();
      if (this.task_id) fd.append('task_id', this.task_id);
      if (this.fps) fd.append('fps', this.fps);
      if (this.width) fd.append('width', this.width);
      if (this.height) fd.append('height', this.height);
      if (this.infer_model_name) fd.append('infer_model_name', this.infer_model_name);
      if (this.file) fd.append('video', this.file);
      this.hint = this.file ? ('已选择视频：' + this.file.name) : '未选择视频，默认使用当前配置的视频路径（通常是 test.mp4）';
      const r = await fetch('/start_send', { method: 'POST', body: fd });
      const j = await r.json();
      if (!j.ok) {
        this.hint = '启动失败: ' + (j.error || 'unknown');
        alert(this.hint);
      } else {
        this.hint = '发送已启动，task_id=' + j.task_id + ', video=' + j.video_path;
      }
      this.refresh();
    }
  },
  mounted() { this.refresh(); setInterval(() => this.refresh(), 1000); }
}).mount('#app');
</script>
</body>
</html>
"""


@sender_app.post("/start_send")
async def start_send(
    task_id: str = Form(""),
    fps: float = Form(0),
    width: int = Form(0),
    height: int = Form(0),
    infer_model_name: str = Form(""),
    video: UploadFile | None = File(default=None),
):
    if sender_state is None:
        return JSONResponse({"ok": False, "error": "sender not initialized"}, status_code=500)
    with sender_state.lock:
        if sender_state.running:
            return JSONResponse({"ok": False, "error": "sender is running"}, status_code=409)
    selected_task_id = task_id.strip() or sender_state.task_id
    selected_video_path = sender_state.video_path
    if video is not None and video.filename:
        target = sender_state.upload_dir / f"{int(time.time())}_{video.filename}"
        data = await video.read()
        target.write_bytes(data)
        selected_video_path = str(target)

    cfg = parse_sender_args()
    cfg.task_id = selected_task_id
    cfg.video_path = selected_video_path
    if fps > 0:
        cfg.fps = float(fps)
    if width > 0:
        cfg.width = int(width)
    if height > 0:
        cfg.height = int(height)
    if infer_model_name.strip():
        name = infer_model_name.strip()
        if name in SUPPORTED_MODEL_ALIASES:
            cfg.infer_model_name = name
    worker = Thread(target=send_frames, args=(cfg, sender_state), daemon=True)
    sender_state.worker = worker
    worker.start()
    return {"ok": True, "task_id": selected_task_id, "video_path": selected_video_path}


if __name__ == "__main__":
    main()
