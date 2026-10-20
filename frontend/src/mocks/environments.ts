import { fixture, scenarios } from "./fixtures";
import type { Workspace, OptionView } from "../types";
export const environments = [
  {
    id: "SC0",
    label: "정상 운영",
    tag: "기본 환경",
    description: "예정된 생산과 부품 입고를 확인합니다.",
  },
  {
    id: "SC1",
    label: "허브 생산 당김",
    tag: "Case 1",
    description: "앞당긴 허브 생산이 공용 MCU와 다른 생산에 영향을 줍니다.",
  },
  {
    id: "SC2",
    label: "주문 수량 변경",
    tag: "Case 2",
    description: "스위치 증산과 플러그 감산으로 부품 소요가 달라집니다.",
  },
  {
    id: "SC3",
    label: "생산 지연 · 설계변경",
    tag: "Case 3",
    description: "도어센서 생산 지연이 케이스 설계변경 적용일을 넘습니다.",
  },
  {
    id: "SC4",
    label: "협력사 입고 지연",
    tag: "Case 4",
    description: "늦어진 릴레이 납품이 스위치 생산에 영향을 줍니다.",
  },
];
export function environmentFixture(id: string): Workspace {
  const w = fixture("APPROVER");
  const sc = scenarios.find((s) => s.id === id);
  if (!sc) throw new Error("알 수 없는 업무 환경입니다.");
  w.environment = id;
  w.issues = structuredClone(sc.issues);
  w.policyVersion = sc.policy_version;
  w.analysisStale = false;
  w.history = [
    {
      id: `${id}-start`,
      at: "2026-11-02T09:00:00+09:00",
      title: id === "SC0" ? "기준 생산·입고 일정 확인" : sc.name,
      target: id === "SC0" ? "생산계획 7건" : sc.change,
      status: "완료",
      detail: "김계획 · 업무 환경 시작",
    },
  ];
  if (id === "SC0") {
    w.options = [];
    return w;
  }
  if (id === "SC1") w.plans.find((p) => p.id === "PP-05")!.date = "2026-11-25";
  if (id === "SC2") {
    w.plans.find((p) => p.id === "PP-03")!.qty = 900;
    w.plans.find((p) => p.id === "PP-04")!.qty = 500;
  }
  if (id === "SC3") w.plans.find((p) => p.id === "PP-02")!.date = "2027-01-04";
  if (id === "SC4") {
    const p = w.purchases.find((p) => p.id === "PO-2610-002")!;
    p.date = "2026-11-27";
    p.status = "지연 통보";
  }
  function option(
    n: number,
    name: string,
    item: string,
    po: string,
    qty: number,
    date: string,
    action: "split" | "new-po",
    impact: string,
    cost: number,
  ): OptionView {
    return {
      id: `${id}-OPT-${n}`,
      action,
      revision: 1,
      name,
      item,
      po,
      qty,
      date,
      resolves: id === "SC4",
      verification: id === "SC4" ? "VERIFIED" : "PENDING",
      impact,
      risk: "협력사 확약과 재분석 필요",
      confirmed: false,
      review: "미검수",
      role: "APPROVER",
      addedCost: cost,
      costEvidence: "환경별 가상 견적 · 실제 확약 전 검수 필요",
      unitPrice: 0,
    };
  }
  if (id === "SC2")
    w.options = [
      option(
        1,
        "릴레이 400개 선입고",
        "릴레이",
        "PO-2610-002",
        400,
        "2026-11-23",
        "split",
        "릴레이 부족 해소 후보 · 전원 모듈 조달 별도",
        180000,
      ),
      option(
        2,
        "전원 모듈 200개 선입고",
        "전원 모듈",
        "PO-2610-003",
        200,
        "2026-11-23",
        "split",
        "전원 부족 해소 후보 · 릴레이 조달 별도",
        120000,
      ),
    ];
  if (id === "SC3")
    w.options = [
      option(
        1,
        "신형 케이스 800개 조달",
        "도어센서 케이스 Rev.B",
        "신규 발주",
        800,
        "2027-01-04",
        "new-po",
        "신형 소요 확보 후보 · 구형 800개 사용 제한 유지",
        0,
      ),
    ];
  if (id === "SC4")
    w.options = [
      option(
        1,
        "릴레이 1,000개 선입고",
        "릴레이",
        "PO-2610-002",
        1000,
        "2026-11-23",
        "split",
        "스위치 생산 유지 후보 · 잔량 200개 11/27 입고",
        400000,
      ),
    ];
  return w;
}
