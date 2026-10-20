<script setup lang="ts">
import { computed, ref } from "vue";
import { useWorkspace } from "../stores/workspace";
import type { Item } from "../types";
import Drawer from "../components/Drawer.vue";
import Badge from "../components/Badge.vue";
const w = useWorkspace(),
  q = ref(""),
  type = ref("PART"),
  tab = ref("재고");
const selected = ref<Item>();
const product = ref("IoT 허브");
const rows = computed(() =>
  w.data!.items.filter(
    (i) => i.type === type.value && (i.id + i.name).includes(q.value),
  ),
);
const flow = [
  { date: "2026-11-23", event: "기존 생산 후", base: 300, after: 300 },
  { date: "2026-11-25", event: "허브 400대 선생산", base: 300, after: -100 },
  { date: "2026-11-27", event: "MCU 500개 입고", base: 800, after: 400 },
  { date: "2026-11-30", event: "플러그 생산", base: 400, after: -100 },
  { date: "2026-12-04", event: "후속 입고 (시연)", base: 900, after: 400 },
];
function path(key: "base" | "after") {
  return flow
    .map(
      (p, i) =>
        `${i === 0 ? "M" : "H"} ${50 + i * 110} ${i === 0 ? 160 - p[key] / 7 : "V " + (160 - p[key] / 7)}`,
    )
    .join(" ");
}
function open(i: Item) {
  selected.value = i;
  w.context = `${i.id} · ${i.name}`;
}
</script>
<template>
  <div class="tabs">
    <button
      v-for="t in ['재고', '제품별 BOM']"
      :class="{ active: tab === t }"
      @click="tab = t"
    >
      {{ t }}
    </button>
  </div>
  <template v-if="tab === '재고'"
    ><div class="toolbar">
      <select v-model="type" aria-label="재고 구분">
        <option value="PART">구매부품 재고</option>
        <option value="FG">완제품 재고</option></select
      ><input
        v-model="q"
        aria-label="품목 검색"
        placeholder="품목코드 또는 품목명 검색"
      />
    </div>
    <div class="card flush table-wrap">
      <table>
        <thead>
          <tr>
            <th>품목</th>
            <th class="num">현재 재고</th>
            <th class="num">안전재고</th>
            <th>협력사</th>
            <th>리드타임</th>
            <th>상세</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="i in rows" :key="i.id">
            <td>
              <b>{{ i.name }}</b
              ><small
                >{{ i.id }}
                <span v-if="i.shared.length > 1"
                  >· 공용 {{ i.shared.length }}개 제품</span
                ></small
              >
            </td>
            <td class="num">{{ i.stock.toLocaleString() }}</td>
            <td class="num">{{ i.safety.toLocaleString() }}</td>
            <td>{{ i.supplier }}</td>
            <td>{{ i.lead }}일</td>
            <td><button @click="open(i)">재고 상세</button></td>
          </tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="empty">검색 결과가 없습니다.</div>
    </div></template
  >
  <section v-else class="card">
    <div class="toolbar">
      <select v-model="product" aria-label="BOM 제품 선택">
        <option
          v-for="p in [
            'IoT 허브',
            '스마트 플러그',
            '스마트 스위치',
            '도어센서',
          ]"
        >
          {{ p }}
        </option></select
      ><span class="caption">데모 BOM</span>
    </div>
    <details open class="bom-root">
      <summary>{{ product }} · 완제품</summary>
      <div class="bom-children">
        <details open>
          <summary>
            전자부품 그룹
            <span class="caption">구조 노드 · 재고관리 대상 아님</span>
          </summary>
          <button
            v-for="i in w.data?.items.filter(
              (i) => i.shared.includes(product) && !i.id.startsWith('CASE'),
            )"
            @click="open(i)"
          >
            <span>{{ i.id }} · {{ i.name }}</span
            ><Badge :text="i.shared.length > 1 ? '공용 부품' : '구매 부품'" />
          </button>
        </details>
        <details v-if="product === '도어센서'" open>
          <summary>케이스 · ECN-2026-014</summary>
          <button
            v-for="i in w.data?.items.filter((i) => i.id.startsWith('CASE'))"
            @click="open(i)"
          >
            {{ i.name }} ·
            {{ i.id === "CASE-A" ? "2026-11-30까지" : "2026-12-01부터" }}
          </button>
        </details>
      </div>
    </details>
    <p class="caption">전자부품 그룹은 구조 표시용입니다.</p>
  </section>
  <Drawer v-if="selected" :title="selected.name" @close="selected = undefined"
    ><span class="eyebrow"
      >{{ selected.id }} ·
      {{ selected.type === "FG" ? "완제품" : "구매부품" }}</span
    >
    <dl>
      <dt>현재 재고</dt>
      <dd>{{ selected.stock.toLocaleString() }}개</dd>
      <dt>안전재고</dt>
      <dd>{{ selected.safety }}개</dd>
      <dt>연결 제품</dt>
      <dd>{{ selected.shared.join(" · ") || "완제품" }}</dd>
    </dl>
    <h3>로트 · 보관 위치</h3>
    <div class="card">
      <b>LOT-{{ selected.id }}-01</b>
      <p>가온 1창고 / A-01 · {{ selected.stock }}개</p>
      <Badge text="검수 합격" /><small>최소 시연 재고 로트</small>
    </div>
    <div
      v-if="
        selected.id === 'MCU' && w.scenario === 'SC1' && !w.data?.analysisStale
      "
    >
      <h3>예상재고 흐름 · 허브 생산 당김</h3>
      <p class="caption">
        기준 계획 — 회색 / 변경 후 — 진회색 · 안전재고 300개
      </p>
      <svg
        viewBox="0 0 540 230"
        role="img"
        aria-label="MCU 예상 재고 계단형 차트. 11월 25일 부족, 27일 회복, 30일 재부족, 12월 4일 회복. 아래 표에 동일 수치 제공."
      >
        <line x1="45" y1="160" x2="510" y2="160" stroke="#c84a47" />
        <text x="2" y="164">0</text>
        <line
          x1="45"
          :y1="160 - 300 / 7"
          x2="510"
          :y2="160 - 300 / 7"
          stroke="#b7791f"
          stroke-dasharray="5 4"
        />
        <text x="0" :y="160 - 300 / 7">300</text>
        <path :d="path('base')" fill="none" stroke="#aab5c3" stroke-width="3" />
        <path
          :d="path('after')"
          fill="none"
          stroke="#46584b"
          stroke-width="3"
        />
        <text v-for="(p, n) in flow" :x="30 + n * 110" y="210" font-size="12">
          {{ p.date.slice(5) }}
        </text>
      </svg>
      <p class="warning-box">
        부족: 2026-11-25 / 회복: 11-27 · 재부족: 11-30 / 회복: 12-04
      </p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>일자 / 이벤트</th>
              <th class="num">기준</th>
              <th class="num">변경 후</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in flow">
              <td>
                {{ p.date }}<small>{{ p.event }}</small>
              </td>
              <td class="num">{{ p.base }}</td>
              <td class="num">{{ p.after }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <small
        >demo-flow-SC1-v1 · 상세 흐름은 독립 시연 fixture이며 서버 계산값이
        아닙니다.</small
      >
    </div>
    <p v-else class="empty">
      이 품목의 재고 흐름은 분석 API 연결 후 제공합니다.
    </p>
    <p v-if="selected.id.startsWith('CASE')" class="warning-box">
      ECN-2026-014 · 3.2 · 시연 문서 v1<br />2026-12-01 이후 생산은 Rev.B를
      사용합니다. 구형 케이스 사용 제한과 일반 과잉은 별도 판정입니다.
    </p></Drawer
  >
</template>
