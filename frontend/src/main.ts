import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import Overview from "./views/Overview.vue";
import Plans from "./views/Plans.vue";
import Materials from "./views/Materials.vue";
import Purchasing from "./views/Purchasing.vue";
import Solutions from "./views/Solutions.vue";
import Approvals from "./views/Approvals.vue";
import History from "./views/History.vue";
import "./style.css";
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: Overview, meta: { title: "업무 현황" } },
    { path: "/plans", component: Plans, meta: { title: "생산계획" } },
    { path: "/materials", component: Materials, meta: { title: "자재 · BOM" } },
    {
      path: "/purchasing",
      component: Purchasing,
      meta: { title: "발주 · 입고" },
    },
    {
      path: "/solutions",
      component: Solutions,
      meta: { title: "해결안 비교" },
    },
    {
      path: "/approvals",
      component: Approvals,
      meta: { title: "승인 대기함" },
    },
    { path: "/history", component: History, meta: { title: "작업 이력" } },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
createApp(App).use(createPinia()).use(router).mount("#app");
