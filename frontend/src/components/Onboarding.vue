<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import {
  Hexagon,
  ArrowRight,
  Check,
  Users,
  BriefcaseBusiness,
} from "lucide-vue-next";
import { useWorkspace, names } from "../stores/workspace";
import { environments } from "../mocks/environments";
import { login, mode } from "../services/api";
import type { Role } from "../types";
const w = useWorkspace(),
  router = useRouter(),
  stage = ref(0),
  role = ref<Role>("PLANNER"),
  environment = ref("SC0"),
  username = ref(""),
  password = ref(""),
  error = ref(""),
  busy = ref(false);
const roles = [
  {
    id: "PLANNER" as Role,
    label: "생산관리",
    description: "생산 일정과 해결안 우선순위 결정",
  },
  {
    id: "BUYER" as Role,
    label: "구매 담당자",
    description: "입고 관리와 비용등급 검수",
  },
  {
    id: "APPROVER" as Role,
    label: "구매팀장",
    description: "변경 승인과 실행 결과 확인",
  },
];
async function auth() {
  busy.value = true;
  error.value = "";
  try {
    const s = await login(username.value, password.value);
    await w.enter(s);
    password.value = "";
    router.push("/");
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function enter() {
  busy.value = true;
  await w.enter(
    { role: role.value, name: names[role.value] },
    environment.value,
  );
  await router.push("/");
  busy.value = false;
}
</script>
<template>
  <div class="onboarding">
    <header class="entry-header">
      <div class="brand"><Hexagon :size="24" />BOM Agent</div>
      <span>가온스마트 · 생산·구매 협업 공간</span>
    </header>
    <main class="entry-main">
      <div class="entry-intro">
        <span class="eyebrow">GAON SMART WORKSPACE</span>
        <h1>
          {{
            stage === 0
              ? "업무 공간에 오신 것을 환영합니다"
              : stage === 1
                ? "어떤 업무를 담당하시나요?"
                : "어떤 업무 환경에서 시작할까요?"
          }}
        </h1>
        <p>
          생산계획과 부품 입고를 연결하고, 변경으로 생기는 위험을 함께
          해결합니다.
        </p>
      </div>
      <ol class="entry-steps">
        <li
          v-for="(label, n) in ['진입 방식', '담당 역할', '업무 환경']"
          :class="{ current: stage === n, done: stage > n }"
        >
          <span>{{ n + 1 }}</span
          >{{ label }}
        </li>
      </ol>
      <section v-if="stage === 0" class="entry-login">
        <form @submit.prevent="auth">
          <h2>회사 계정 로그인</h2>
          <label
            >아이디<input
              v-model="username"
              required
              autocomplete="username" /></label
          ><label
            >비밀번호<input
              v-model="password"
              required
              type="password"
              autocomplete="current-password" /></label
          ><button class="primary wide" :disabled="busy">
            {{ busy ? "로그인 중…" : "로그인" }}
          </button>
          <p v-if="error" class="error" role="alert">{{ error }}</p>
        </form>
        <div v-if="mode === 'mock'" class="entry-demo">
          <BriefcaseBusiness :size="26" />
          <h2>데모 업무 공간</h2>
          <p>
            가상 데이터로 역할별 업무를 확인하세요.<br />실제 계정 인증 없이
            이용할 수 있습니다.
          </p>
          <button @click="stage = 1">데모 시작 <ArrowRight :size="16" /></button
          ><small>선택한 환경의 데이터만 변경됩니다.</small>
        </div>
      </section>
      <template v-else-if="stage === 1"
        ><div class="role-choices">
          <button
            v-for="r in roles"
            :key="r.id"
            :class="{ selected: role === r.id }"
            :aria-pressed="role === r.id"
            @click="role = r.id"
          >
            <Users :size="21" /><b>{{ r.label }}</b
            ><span>{{ names[r.id] }}</span
            ><small>{{ r.description }}</small
            ><Check v-if="role === r.id" class="choice-check" :size="18" />
          </button>
        </div>
        <footer class="entry-footer">
          <button @click="stage = 0">이전</button
          ><button class="primary" @click="stage = 2">
            업무 환경 선택 <ArrowRight :size="16" />
          </button></footer></template
      ><template v-else
        ><div class="environment-choices">
          <button
            v-for="e in environments"
            :key="e.id"
            :class="{ selected: environment === e.id }"
            :aria-pressed="environment === e.id"
            @click="environment = e.id"
          >
            <span class="environment-tag">{{ e.tag }}</span>
            <div>
              <b>{{ e.label }}</b>
              <p>{{ e.description }}</p>
            </div>
            <span class="radio-indicator"
              ><Check v-if="environment === e.id" :size="14"
            /></span>
          </button>
        </div>
        <footer class="entry-footer">
          <button @click="stage = 1">역할 다시 선택</button
          ><span>{{ names[role] }} · 데모</span
          ><button class="primary" :disabled="busy" @click="enter">
            {{ busy ? "불러오는 중…" : "업무 공간 들어가기" }}
            <ArrowRight :size="16" />
          </button></footer
      ></template>
    </main>
    <footer class="entry-bottom">
      가온스마트 / BOM Agent <span>시연 기준일 2026.11.02</span>
    </footer>
  </div>
</template>
