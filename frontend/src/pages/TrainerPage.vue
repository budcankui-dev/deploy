<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";

const apiBase = ref(localStorage.getItem("trainer_api") || window.location.origin);
const data = ref({});
const liveText = ref("实时连接中...");
const es = ref(null);

const saveBase = () => localStorage.setItem("trainer_api", apiBase.value);

const refresh = async () => {
  const res = await fetch(`${apiBase.value}/metrics?_=${Date.now()}`);
  data.value = await res.json();
};

const connectSSE = () => {
  if (es.value) es.value.close();
  es.value = new EventSource(`${apiBase.value}/metrics_sse`);
  es.value.onopen = () => (liveText.value = "实时连接已建立");
  es.value.onerror = () => (liveText.value = "实时连接重试中...");
  es.value.onmessage = (evt) => {
    data.value = JSON.parse(evt.data);
  };
};

const start = async () => {
  const fd = new FormData();
  if (data.value.epochs) fd.append("epochs", data.value.epochs);
  if (data.value.batch_size) fd.append("batch_size", data.value.batch_size);
  if (data.value.learning_rate) fd.append("learning_rate", data.value.learning_rate);
  if (data.value.model_name) fd.append("model_name", data.value.model_name);
  await fetch(`${apiBase.value}/start_train`, { method: "POST", body: fd });
  await refresh();
};

onMounted(async () => {
  await refresh();
  connectSSE();
});

onBeforeUnmount(() => {
  if (es.value) es.value.close();
});
</script>

<template>
  <section>
    <div class="toolbar">
      <label>训练 API</label>
      <input v-model="apiBase" @change="saveBase(); refresh(); connectSSE()" />
      <span>{{ liveText }}</span>
    </div>
    <div class="cards">
      <div class="card"><div class="k">任务ID（task_id）</div><div class="v">{{ data.task_id || "-" }}</div></div>
      <div class="card"><div class="k">最新 Epoch</div><div class="v">{{ data.latest_epoch || 0 }}</div></div>
      <div class="card"><div class="k">状态</div><div class="v">{{ data.message || "-" }}</div></div>
    </div>
    <div class="form-grid">
      <label>Epochs<input type="number" v-model.number="data.epochs" /></label>
      <label>Batch Size<input type="number" v-model.number="data.batch_size" /></label>
      <label>Learning Rate<input type="number" step="0.0001" v-model.number="data.learning_rate" /></label>
      <label>Model Name<input v-model="data.model_name" /></label>
      <button :disabled="data.running" @click="start">{{ data.running ? "训练中..." : "开始训练" }}</button>
    </div>
    <pre>{{ JSON.stringify(data, null, 2) }}</pre>
  </section>
</template>

