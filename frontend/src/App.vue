<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  LayoutDashboard,
  CalendarDays,
  Boxes,
  Truck,
  ClipboardCheck,
  History,
  PanelRightOpen,
  LogOut,
  Hexagon,
} from "lucide-vue-next";
import { useWorkspace, names } from "./stores/workspace";
import { mode } from "./services/api";
import type { Role } from "./types";
import AiPanel from "./components/AiPanel.vue";
import Onboarding from "./components/Onboarding.vue";
import Drawer from "./components/Drawer.vue";
import { environments } from "./mocks/environments";
const environmentMenu = ref(false);
const w = useWorkspace(),
  route = useRoute(),
  router = useRouter();
const nav = [
  ["/", "업무 현황", LayoutDashboard],
  ["/plans", "생산계획", CalendarDays],
  ["/materials", "자재 · BOM", Boxes],
  ["/purchasing", "발주 · 입고", Truck],
  ["/approvals", "승인 대기함", ClipboardCheck],
  ["/history", "작업 이력", History],
] as const;
const roleLabel: Record<Role, string> = {
  PLANNER: "생산관리",
  BUYER: "구매 담당",
  APPROVER: "구매팀장",
};
const title = computed(() => route.meta.title);
watch(
  () => route.path,
  (path) => {
    if (path === "/approvals") w.panel = false;
  },
  { immediate: true },
);
watch(
  () => w.epoch,
  () => {
    environmentMenu.value = false;
  },
);
window.addEventListener("session-expired", () => {
  w.clear();
});
</script>
<template>
  <Onboarding v-if="!w.session" />
  <div v-else class="shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/"
        ><Hexagon :size="27" /><span
          >BOM Agent<small>MANUFACTURING WORKSPACE</small></span
        ></RouterLink
      >
      <div class="workspace-label">가온스마트 <span>WORKSPACE</span></div>
      <nav aria-label="주 탐색">
        <RouterLink
          v-for="[path, label, icon] in nav"
          :key="path"
          :to="path"
          :class="{ active: route.path === path }"
          ><component :is="icon" :size="18" />{{ label
          }}<span
            v-if="
              path === '/approvals' &&
              w.data?.approvals.filter((a) => a.status === 'PENDING').length
            "
            class="nav-count"
            >{{
              w.data.approvals.filter((a) => a.status === "PENDING").length
            }}</span
          ></RouterLink
        >
      </nav>
      <div class="sidebar-bottom">
        <span class="connection"
          >● {{ mode === "mock" ? "데모 데이터 연결" : "업무 API 모드" }}</span
        >
        <p>계획과 근거를 연결하는<br />생산·구매 의사결정</p>
        <small>GAON SMART © 2026</small>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <span
          >워크스페이스 <span class="slash">/</span> <b>{{ title }}</b></span
        >
        <div class="top-actions">
          <button
            v-if="mode === 'mock'"
            class="environment-switch"
            @click="environmentMenu = true"
          >
            {{ environments.find((e) => e.id === w.scenario)?.label }}
            <span class="badge">데모</span></button
          ><span class="baseline">기준일 <b>2026.11.02</b></span
          ><select
            v-if="mode === 'mock'"
            :value="w.session.role"
            aria-label="시연 역할 전환"
            @change="
              w.enter({
                role: ($event.target as HTMLSelectElement).value as Role,
                name: names[($event.target as HTMLSelectElement).value as Role],
              })
            "
          >
            <option
              v-for="r in ['PLANNER', 'BUYER', 'APPROVER'] as Role[]"
              :value="r"
            >
              {{ names[r] }} · {{ roleLabel[r] }}
            </option></select
          ><span v-else>{{ w.session.name }}</span
          ><button class="icon-button" aria-label="로그아웃" @click="w.clear">
            <LogOut :size="17" />
          </button>
        </div>
      </header>
      <div class="body-layout">
        <main>
          <div class="page-heading">
            <div>
              <span class="eyebrow"
                >OPERATIONS /
                {{
                  route.path === "/"
                    ? "OVERVIEW"
                    : route.path.slice(1).toUpperCase()
                }}</span
              >
              <h1>{{ title }}</h1>
            </div>
            <button v-if="!w.panel" @click="w.panel = true">
              <PanelRightOpen :size="16" /> AI에 질문
            </button>
          </div>
          <div v-if="w.error" class="error" role="alert">
            {{ w.error }}<button @click="w.load">다시 불러오기</button>
          </div>
          <div v-if="w.notice" class="toast" role="status">
            {{ w.notice
            }}<button class="text-button" @click="w.notice = ''">닫기</button>
          </div>
          <div v-if="w.busy && !w.data" class="empty" role="status">
            업무 데이터를 불러오는 중…
          </div>
          <RouterView v-if="w.data" :key="w.epoch" />
          <div v-else-if="!w.busy" class="empty">
            연결된 업무 데이터가 없습니다. API 연결 상태를 확인하세요.
          </div>
        </main>
        <AiPanel v-if="w.panel" :key="w.epoch" />
      </div>
    </div>
    <Drawer
      v-if="environmentMenu"
      title="업무 환경"
      @close="environmentMenu = false"
      ><div class="environment-choices">
        <button
          v-for="e in environments"
          :key="e.id"
          :class="{ selected: w.scenario === e.id }"
          @click="
            async () => {
              environmentMenu = false;
              await w.switchEnvironment(e.id);
              router.push('/');
            }
          "
        >
          <span class="environment-tag">{{ e.tag }}</span>
          <div>
            <b>{{ e.label }}</b>
            <p>{{ e.description }}</p>
          </div>
        </button>
      </div>
      <button
        class="wide"
        @click="
          async () => {
            environmentMenu = false;
            await w.reset();
          }
        "
      >
        현재 환경만 초기화
      </button></Drawer
    >
  </div>
</template>
