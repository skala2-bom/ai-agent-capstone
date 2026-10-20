<script setup lang="ts">
import { computed, ref } from "vue";
import { ChevronLeft, ChevronRight } from "lucide-vue-next";
import { calendarDays, movePeriod } from "../services/calendar";
import type { Plan, Purchase } from "../types";
const props = defineProps<{
  plans: Plan[];
  total: number;
  purchases: Purchase[];
}>();
defineEmits<{ select: [p: Plan]; receipt: [p: Purchase] }>();
const view = ref<"month" | "week">("month"),
  anchor = ref("2026-11-02"),
  showReceipts = ref(false),
  expanded = ref("");
const days = computed(() => calendarDays(anchor.value, view.value));
const label = computed(() =>
  view.value === "month"
    ? `${anchor.value.slice(0, 4)}년 ${Number(anchor.value.slice(5, 7))}월`
    : `${days.value[0]} — ${days.value[6]}`,
);
const on = (date: string) =>
  props.plans
    .filter((p) => p.date === date)
    .sort((a, b) => a.product.localeCompare(b.product));
const incoming = (date: string) =>
  showReceipts.value ? props.purchases.filter((p) => p.date === date) : [];
</script>
<template>
  <div class="calendar-toolbar">
    <div class="period-nav">
      <h2>{{ label }}</h2>
      <button
        aria-label="이전 기간"
        @click="anchor = movePeriod(anchor, view, -1)"
      >
        <ChevronLeft :size="16" /></button
      ><button
        aria-label="다음 기간"
        @click="anchor = movePeriod(anchor, view, 1)"
      >
        <ChevronRight :size="16" /></button
      ><button @click="anchor = '2026-11-02'">기준일</button>
    </div>
    <div class="calendar-controls">
      <label
        ><input v-model="showReceipts" type="checkbox" />입고 함께 보기</label
      >
      <div class="segmented">
        <button :class="{ active: view === 'month' }" @click="view = 'month'">
          월간</button
        ><button :class="{ active: view === 'week' }" @click="view = 'week'">
          주간
        </button>
      </div>
    </div>
  </div>
  <div class="calendar-legend">
    <span><i />생산</span
    ><span v-if="showReceipts"><i class="receipt-dot" />입고</span
    ><small
      >전체 {{ total }}건 · 필터 적용 {{ plans.length }}건 · 기준일
      2026.11.02</small
    >
  </div>
  <div class="calendar-scroll">
    <div class="calendar-grid" :class="{ weekly: view === 'week' }">
      <div
        v-for="d in ['일', '월', '화', '수', '목', '금', '토']"
        class="weekday"
      >
        {{ d }}
      </div>
      <div
        v-for="day in days"
        :key="day"
        class="calendar-day"
        :class="{
          outside: day.slice(0, 7) !== anchor.slice(0, 7),
          today: day === '2026-11-02',
        }"
      >
        <div class="day-heading">
          <span>{{ Number(day.slice(8)) }}</span
          ><small v-if="on(day).length"
            >{{ on(day).length }}건 ·
            {{
              on(day)
                .reduce((n, p) => n + p.qty, 0)
                .toLocaleString()
            }}대</small
          >
        </div>
        <button
          v-for="p in on(day).slice(0, expanded === day ? 99 : 2)"
          :key="p.id"
          :aria-label="`${p.date} ${p.product} ${p.qty}대 상세`"
          class="calendar-event"
          @click="$emit('select', p)"
        >
          <b>{{ p.product }}</b
          ><span>{{ p.qty.toLocaleString() }}대</span></button
        ><button
          v-if="on(day).length > 2 && expanded !== day"
          class="more-events"
          @click="expanded = day"
        >
          외 {{ on(day).length - 2 }}건</button
        ><button
          v-for="p in incoming(day)"
          :key="p.id"
          class="calendar-event receipt-event"
          @click="$emit('receipt', p)"
        >
          <b>입고 · {{ p.item }}</b
          ><span>{{ p.qty.toLocaleString() }}개</span>
        </button>
      </div>
    </div>
  </div>
</template>
