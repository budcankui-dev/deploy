<script setup>
import { inject } from "vue";
const activeRoutes = inject("activeRoutes", []);
const labels = { "/receiver": "接收节点", "/sender": "发送节点", "/trainer": "训练节点" };
</script>

<template>
  <div class="layout">
    <header class="topbar">
      <h3>任务节点看板（前后端分离）</h3>
      <nav>
        <RouterLink v-for="r in activeRoutes" :key="r.path" :to="r.path">{{ labels[r.path] || r.path }}</RouterLink>
      </nav>
    </header>
    <main class="content">
      <RouterView />
    </main>
  </div>
</template>
