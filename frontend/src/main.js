import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import ReceiverPage from "./pages/ReceiverPage.vue";
import SenderPage from "./pages/SenderPage.vue";
import TrainerPage from "./pages/TrainerPage.vue";
import "./styles.css";

const ROLE = import.meta.env.VITE_APP_ROLE || "all";

const roleGroups = {
  receiver: ["receiver"],
  sender: ["sender"],
  trainer: ["trainer"],
  all: ["receiver", "sender", "trainer"],
};

const allRoutes = [
  { path: "/receiver", component: ReceiverPage, meta: { id: "receiver" } },
  { path: "/sender", component: SenderPage, meta: { id: "sender" } },
  { path: "/trainer", component: TrainerPage, meta: { id: "trainer" } },
];

const allowedIds = roleGroups[ROLE] || roleGroups.all;
const activeRoutes = allRoutes.filter((r) => allowedIds.includes(r.meta.id));
const defaultMap = { receiver: "/receiver", sender: "/sender", trainer: "/trainer" };
const defaultPath = defaultMap[ROLE] || activeRoutes[0]?.path || "/receiver";

const routes = [{ path: "/", redirect: defaultPath }, ...activeRoutes];

const router = createRouter({ history: createWebHistory(), routes });

const app = createApp(App);
app.provide("appRole", ROLE);
app.provide("activeRoutes", activeRoutes);
app.use(router).mount("#app");
