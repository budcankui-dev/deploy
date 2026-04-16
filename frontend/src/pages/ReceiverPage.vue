<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

const apiBase = ref(localStorage.getItem("receiver_api") || window.location.origin);
const current = ref({});
const liveText = ref("实时连接中...");
const es = ref(null);
const inferCanvas = ref(null);
const rttCanvas = ref(null);

const saveBase = () => localStorage.setItem("receiver_api", apiBase.value);
const hasFrame = computed(() => (current.value.latest_frame_id ?? -1) >= 0 && (current.value.count ?? 0) > 0);
const streamUrl = computed(() => `${apiBase.value}/stream.mjpg?_=${Date.now()}`);
const fmt = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));
const backendLabels = { box: "固定框", random_box: "随机框", yolo: "YOLO 目标检测" };
const displayTaskId = computed(() => current.value.task_id || "—");
const metaText = computed(() => {
  const p = current.value.profile || {};
  const res = p.width && p.height ? `${p.width}x${p.height}` : "—";
  const backend = current.value.infer_backend || "";
  const latency = p.target_latency_ms ?? current.value.receiver_target_latency_ms;
  return [
    `任务ID(task_id)=${current.value.task_id || "—"}`,
    `帧率(FPS)=${p.fps ?? "—"} 分辨率(Resolution)=${res}`,
    `推理模型(Model)=${p.infer_model_name || current.value.infer_model_name || "—"} 推理方式(Backend)=${backendLabels[backend] || backend || "—"}`,
    `目标时延(Target Latency, ms)=${latency != null && latency > 0 ? latency : "无"}`,
  ].join("\n");
});

function drawChart(canvas, data, color, label) {
  if (!canvas || !data || data.length === 0) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const pad = { top: 24, right: 12, bottom: 28, left: 52 };
  const cw = w - pad.left - pad.right;
  const ch = h - pad.top - pad.bottom;
  const maxVal = Math.max(...data) * 1.15 || 1;
  const minVal = Math.min(0, Math.min(...data));

  ctx.fillStyle = "#f8f9fa";
  ctx.fillRect(pad.left, pad.top, cw, ch);

  ctx.font = "bold 12px sans-serif";
  ctx.fillStyle = "#333";
  ctx.fillText(`${label}（最近 ${data.length} 个采样点）`, pad.left, 16);

  ctx.strokeStyle = "#e0e0e0";
  ctx.lineWidth = 0.5;
  const gridLines = 4;
  for (let i = 0; i <= gridLines; i++) {
    const y = pad.top + (ch / gridLines) * i;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + cw, y);
    ctx.stroke();
    const val = maxVal - ((maxVal - minVal) / gridLines) * i;
    ctx.fillStyle = "#999";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(val.toFixed(2), pad.left - 4, y + 3);
  }

  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.lineJoin = "round";
  for (let i = 0; i < data.length; i++) {
    const x = pad.left + (i / Math.max(data.length - 1, 1)) * cw;
    const y = pad.top + ch - ((data[i] - minVal) / (maxVal - minVal)) * ch;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  ctx.fillStyle = "#aaa";
  ctx.font = "10px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("ms", pad.left - 4, pad.top - 4);
}

watch(
  () => [current.value.infer_series_ms, current.value.rtt_series_ms],
  () => {
    nextTick(() => {
      drawChart(inferCanvas.value, current.value.infer_series_ms || [], "#1a73e8", "推理耗时趋势（Infer ms）");
      drawChart(rttCanvas.value, current.value.rtt_series_ms || [], "#e8711a", "RTT 时延趋势（RTT ms）");
    });
  },
  { deep: true }
);

const connectSSE = () => {
  if (es.value) es.value.close();
  es.value = new EventSource(`${apiBase.value}/metrics_sse`);
  es.value.onopen = () => (liveText.value = "实时连接已建立");
  es.value.onerror = () => (liveText.value = "实时连接重试中...");
  es.value.onmessage = (evt) => {
    const j = JSON.parse(evt.data);
    current.value = j;
  };
};

const refreshOnce = async () => {
  const res = await fetch(`${apiBase.value}/metrics?_=${Date.now()}`);
  const j = await res.json();
  current.value = j;
};

onMounted(async () => {
  await refreshOnce();
  connectSSE();
});

onBeforeUnmount(() => {
  if (es.value) es.value.close();
});
</script>

<template>
  <section>
    <div class="toolbar">
      <label>接收 API</label>
      <input v-model="apiBase" @change="saveBase(); connectSSE()" />
      <label>当前任务 ID</label>
      <input :value="displayTaskId" readonly style="min-width: 180px" />
      <span>{{ liveText }}</span>
    </div>
    <div class="cards">
      <div class="card"><div class="k">帧数（Frames）</div><div class="v">{{ current.count || 0 }}</div></div>
      <div class="card"><div class="k">推理均值（Infer AVG, ms）</div><div class="v">{{ fmt(current.infer_avg_ms) }}</div></div>
      <div class="card"><div class="k">推理P95（Infer P95, ms）</div><div class="v">{{ fmt(current.infer_p95_ms) }}</div></div>
      <div class="card"><div class="k">时延均值（RTT AVG, ms）</div><div class="v">{{ fmt(current.rtt_avg_ms) }}</div></div>
      <div class="card"><div class="k">时延P95（RTT P95, ms）</div><div class="v">{{ fmt(current.rtt_p95_ms) }}</div></div>
      <div class="card"><div class="k">最新时延（Latest RTT, ms）</div><div class="v">{{ fmt(current.latest_rtt_ms) }}</div></div>
    </div>
    <div class="video-wrap">
      <img v-if="hasFrame" :src="streamUrl" />
      <div v-else>暂无视频帧，等待 sender 发送...</div>
    </div>
    <pre>{{ metaText }}</pre>
    <div class="chart-row">
      <div class="chart-box">
        <canvas ref="inferCanvas"></canvas>
      </div>
      <div class="chart-box">
        <canvas ref="rttCanvas"></canvas>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chart-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}
.chart-box {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 8px;
}
.chart-box canvas {
  width: 100%;
  height: 180px;
}
@media (max-width: 720px) {
  .chart-row { grid-template-columns: 1fr; }
}
</style>
