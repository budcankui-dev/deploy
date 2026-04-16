<script setup>
import { ref } from "vue";

const apiBase = ref(localStorage.getItem("sender_api") || window.location.origin);
const data = ref({});
const form = ref({
  task_id: "",
  receiver_url: "",
  fps: 5,
  width: 640,
  height: 360,
  infer_model_name: "yolov8",
});
const file = ref(null);
const hint = ref("");
const videoChoice = ref("real");
const dirty = ref(false);

const saveBase = () => localStorage.setItem("sender_api", apiBase.value);
const fmt = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));
const refresh = async () => {
  const res = await fetch(`${apiBase.value}/metrics?_=${Date.now()}`);
  data.value = await res.json();

  // 同步表单：仅在用户未编辑（dirty=false）且未上传文件时同步，避免被自动刷新覆盖输入
  if (!dirty.value) {
    form.value.task_id = data.value.task_id || "";
    form.value.receiver_url = data.value.receiver_url || "";
    form.value.fps = Number(data.value.fps || form.value.fps);
    form.value.width = Number(data.value.width || form.value.width);
    form.value.height = Number(data.value.height || form.value.height);
    form.value.infer_model_name = data.value.infer_model_name || form.value.infer_model_name;
  }

  // keep user's selection; but fall back to real if auto is unavailable
  if (!file.value) {
    const vids = data.value.available_videos || [];
    const auto = vids.find((v) => v.id === "auto");
    if (videoChoice.value === "auto" && auto && auto.exists === false) videoChoice.value = "real";
  }
};

const start = async () => {
  const fd = new FormData();
  if (form.value.task_id) fd.append("task_id", form.value.task_id);
  if (form.value.fps) fd.append("fps", String(form.value.fps));
  if (form.value.width) fd.append("width", String(form.value.width));
  if (form.value.height) fd.append("height", String(form.value.height));
  if (form.value.infer_model_name) fd.append("infer_model_name", form.value.infer_model_name);
  if (file.value) fd.append("video", file.value);
  else fd.append("video_choice", videoChoice.value);
  const res = await fetch(`${apiBase.value}/start_send`, { method: "POST", body: fd });
  const j = await res.json();
  hint.value = j.ok ? `发送已启动: ${j.task_id}` : `启动失败: ${j.error || "unknown"}`;
  dirty.value = false;
  await refresh();
};

const stop = async () => {
  const res = await fetch(`${apiBase.value}/stop_send`, { method: "POST" });
  const j = await res.json();
  hint.value = j.ok ? "发送已中止" : `中止失败: ${j.error || "unknown"}`;
  await refresh();
};

const resetForm = () => {
  dirty.value = false;
  file.value = null;
  videoChoice.value = "real";
  refresh();
};

setInterval(refresh, 1000);
refresh();
</script>

<template>
  <section>
    <div class="toolbar">
      <label>发送 API</label>
      <input v-model="apiBase" @change="saveBase(); refresh()" />
      <span>{{ hint }}</span>
    </div>
    <div class="cards">
      <div class="card"><div class="k">任务ID（task_id）</div><div class="v">{{ data.task_id || "-" }}</div></div>
      <div class="card"><div class="k">平均时延（RTT AVG, ms）</div><div class="v">{{ fmt(data.rtt_avg_ms) }}</div></div>
      <div class="card"><div class="k">P95时延（RTT P95, ms）</div><div class="v">{{ fmt(data.rtt_p95_ms) }}</div></div>
      <div class="card"><div class="k">已发送帧（Sent）</div><div class="v">{{ data.sent_frames || 0 }}</div></div>
      <div class="card"><div class="k">成功帧（Success）</div><div class="v">{{ data.success_frames || 0 }}</div></div>
      <div class="card"><div class="k">失败帧（Errors）</div><div class="v">{{ data.error_frames || 0 }}</div></div>
    </div>
    <div class="panel">
      <div class="form-grid">
        <label>任务ID（只读）<input :value="data.task_id || ''" readonly /></label>
        <label>接收地址（只读）<input :value="data.receiver_url || ''" readonly /></label>
        <label>帧率（FPS）<input type="number" v-model.number="form.fps" @input="dirty = true" /></label>
        <label>宽度（px）<input type="number" v-model.number="form.width" @input="dirty = true" /></label>
        <label>高度（px）<input type="number" v-model.number="form.height" @input="dirty = true" /></label>
        <label>目标时延（只读）<input :value="data.target_latency_ms != null && data.target_latency_ms > 0 ? data.target_latency_ms : '无'" readonly /></label>
        <label>模型（Model）
          <select v-model="form.infer_model_name" @change="dirty = true">
            <option value="yolov8">YOLOv8</option>
            <option value="yolov9">YOLOv9</option>
          </select>
        </label>
        <label>视频来源（默认）
          <select v-model="videoChoice" :disabled="!!file">
            <option value="real">真实视频（test.mp4）</option>
            <option value="auto">合成视频（auto_generated.mp4）</option>
          </select>
        </label>
        <label class="wide-field">视频文件（可选，默认：test.mp4）
          <input type="file" @change="file = $event.target.files?.[0] || null" />
        </label>
      </div>
      <div class="action-row">
        <div class="action-hint">
          当前默认：{{ videoChoice === "auto" ? "auto_generated.mp4" : "test.mp4" }}（上传文件会覆盖默认）
        </div>
        <div class="action-buttons">
          <button :disabled="data.running" @click="start">{{ data.running ? "发送中..." : "开始发送" }}</button>
          <button class="secondary" :disabled="!data.running" @click="stop">中止发送</button>
          <button class="secondary" :disabled="data.running" @click="resetForm">重置</button>
        </div>
      </div>
    </div>
    <pre v-if="hint">{{ hint }}</pre>
  </section>
</template>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 10px;
  padding: 14px;
}

.wide-field {
  grid-column: 1 / -1;
}

.action-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #eee;
}

.action-hint {
  color: #555;
  font-size: 14px;
}

.action-buttons {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.action-buttons button {
  min-width: 140px;
}

.secondary {
  background: #f5f5f5;
}

@media (max-width: 720px) {
  .action-row {
    flex-direction: column;
    align-items: stretch;
  }

  .action-buttons {
    width: 100%;
  }

  .action-buttons button {
    flex: 1;
  }
}
</style>
