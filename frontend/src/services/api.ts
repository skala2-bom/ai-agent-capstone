import type { Command, Session, Workspace, Role, Option, Issue, Grade } from "../types";
import { sourceCards } from "./answer";
import { environmentFixture } from "../mocks/environments";
import { mutate } from "./workflow";
import { toTrace } from "./traces";
export const mode = import.meta.env.VITE_DATA_MODE === "api" ? "api" : "mock";
export async function request<T>(
  path: string,
  token?: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    if (r.status === 401) window.dispatchEvent(new Event("session-expired"));
    throw new Error(
      r.status === 401
        ? "로그인이 만료되었습니다. 다시 로그인하세요."
        : r.status === 403
          ? "이 작업에 대한 권한이 없습니다."
          : r.status === 404
            ? "아직 연결되지 않은 API입니다."
            : `요청 실패 (${r.status}). 잠시 후 다시 시도하세요.`,
    );
  }
  return r.status === 204 || r.headers.get("content-length") === "0"
    ? (undefined as T)
    : await r.text().then((t) => (t ? JSON.parse(t) : undefined));
}
export interface Gateway {
  load(s: Session, environment?: string): Promise<Workspace>;
  save(s: Session, c: Command, environment?: string): Promise<Workspace>;
  reset(environment?: string): void;
}
const databases = new Map<string, Workspace>();
function forRole(w: Workspace, role: Role): Workspace {
  const copy = structuredClone(w);
  copy.options = copy.options.map((o) => {
    const { role: _, ...rest } = o;
    const { addedCost, costEvidence, unitPrice, ...safe } =
      rest as typeof rest & {
        addedCost?: number;
        costEvidence?: string;
        unitPrice?: number;
      };
    return role === "PLANNER"
      ? { ...safe, note: undefined, role }
      : role === "BUYER"
        ? {
            ...safe,
            role,
            addedCost: addedCost ?? 0,
            costEvidence: costEvidence ?? "",
          }
        : {
            ...safe,
            role,
            addedCost: addedCost ?? 0,
            costEvidence: costEvidence ?? "",
            unitPrice: unitPrice ?? 0,
          };
  });
  return copy;
}
const mock: Gateway = {
  async load(s, environment = "SC1") {
    if (!databases.has(environment))
      databases.set(environment, environmentFixture(environment));
    return forRole(databases.get(environment)!, s.role);
  },
  async save(s, c, environment = "SC1") {
    const next = structuredClone(
      databases.get(environment) ?? environmentFixture(environment),
    );
    mutate(next, c, s);
    databases.set(environment, next);
    return forRole(next, s.role);
  },
  reset(environment = "SC1") {
    databases.delete(environment);
  },
};
// [로컬 통합 임시] backend 화면 API 응답({plans:[{plan_id,…}]})을 화면 타입 배열로 바꾼다.
// 정식 DTO는 A·B 파트 합의 사항 (contracts 3.1). 합의되면 이 변환을 그 형식에 맞춰 바꾼다.
type Row = Record<string, unknown>;
const PLAN_STATUS: Record<string, string> = { PLANNED: "확정" };
export function toWorkspace(rp: { plans: Row[] }, ri: { items: Row[] }, rpo: { purchase_orders: Row[] }) {
  const names = new Map(ri.items.map((i) => [String(i.item_id), String(i.name)]));
  const plans: Workspace["plans"] = rp.plans.map((p) => ({
    id: String(p.plan_id),
    product: names.get(String(p.product_id)) ?? String(p.product_id),
    date: String(p.plan_date),
    qty: Number(p.qty),
    status: PLAN_STATUS[String(p.status)] ?? String(p.status),
  }));
  const items: Workspace["items"] = ri.items.map((i) => ({
    id: String(i.item_id),
    name: String(i.name),
    type: i.type === "FG" ? "FG" : "PART",
    stock: Number(i.on_hand),
    safety: Number(i.safety_stock ?? 0),
    // 협력사·리드타임·사용 제품(used_in)은 backend가 재고 응답에 더해 준다 (10/20 밤). 완제품은 없다
    supplier: String(i.supplier_name ?? ""),
    lead: Number(i.lead_time_days ?? 0),
    shared: (i.used_in as string[] | undefined) ?? [],
  }));
  const purchases: Workspace["purchases"] = rpo.purchase_orders.map((o) => ({
    id: String(o.po_id),
    item: names.get(String(o.item_id)) ?? String(o.item_id),
    supplier: String(o.supplier_name ?? o.supplier_id),
    qty: Number(o.qty),
    date: String(o.due_date),
    received: 0,
    passed: 0,
    failed: 0,
    status: String(o.status),
  }));
  return { plans, items, purchases };
}
// [로컬 통합 임시] 승인 카드(contracts 3.3)를 화면의 Approval + Option으로 바꾼다.
// 화면 승인함은 optionId로 대안을 찾아 그리므로, 카드마다 대안 하나를 만들어 붙인다 (id = approval_id).
const approvalTasks = new Map<string, string>(); // approval_id → task_id (결정 API 경로에 필요)
/** 승인 카드의 요청자 ID → 화면 이름 (seed의 시연 계정) */
const userNames: Record<string, string> = { planner: "김계획", buyer: "박구매", approver: "정팀장" };
export function toApprovals(cards: Row[], names: Map<string, string>) {
  const approvals: Workspace["approvals"] = [];
  const options: Workspace["options"] = [];
  for (const c of cards) {
    const id = String(c.approval_id);
    const args = (c.args ?? {}) as Row;
    const before = (c.before ?? {}) as Row;
    const action: Option["action"] = ({ split_po: "split", request_due_date_change: "due-date",
      change_po_qty: "qty", cancel_po: "cancel" } as Record<string, Option["action"]>)[String(c.tool)] ?? "reallocate";
    approvalTasks.set(id, String(c.task_id));
    approvals.push({
      id,
      taskId: String(c.task_id),
      optionId: id,
      requester: userNames[String(c.requested_by)] ?? String(c.requested_by),
      at: String(c.requested_at),
      status: c.status as Workspace["approvals"][number]["status"],
      reason: (c.reason as string) ?? undefined,
      beforeQty: before.qty == null ? undefined : Number(before.qty),
      beforeDate: before.due_date == null ? undefined : String(before.due_date),
      evidence: sourceCards(c.evidence),
      problem: (c.problem as string) ?? undefined,
      memo: (c.memo as string) ?? undefined,
    });
    options.push({
      action,
      id,
      revision: 1,
      name: String(c.summary ?? c.tool),
      item: names.get(String(before.item_id)) ?? String(before.item_id),
      po: String(args.po_id),
      qty: Number(args.split_qty ?? args.new_qty ?? before.qty ?? 0),
      date: String(args.new_due_date ?? before.due_date ?? ""),
      resolves: true,
      verification: "VERIFIED",
      impact: action === "qty" ? `발주 수량 ${before.qty} → ${args.new_qty}개`
        : action === "cancel" ? "발주 취소 (CANCELLED)"
        : action === "due-date" ? `입고 예정일 ${before.due_date} → ${args.new_due_date}` : "분할 납품",
      risk: "",
      confirmed: false,
      review: "확정",
      reason: String(c.summary ?? ""),
      role: "APPROVER",
      addedCost: Number(c.amount ?? 0),
      costEvidence: "",
      unitPrice: 0,
    });
  }
  return { approvals, options };
}
/** 작업 상태 → 작업 이력 화면의 상태 글자 (History.vue의 필터와 같은 글자) */
const TASK_STATUS: Record<string, string> = {
  QUEUED: "진행 중",
  RUNNING: "진행 중",
  WAITING_APPROVAL: "승인 대기",
  DONE: "완료",
  FAILED: "실패",
};
/** AI 작업 목록(GET /api/ai/tasks) → 작업 이력. 생산관리는 backend가 자기 작업만 준다 */
export function toHistory(tasks: Row[]): Workspace["history"] {
  return tasks.map((t) => ({
    id: String(t.taskId),
    at: String(t.createdAt),
    title: String(t.question ?? ""),
    target: t.trigger === "SYSTEM" ? "자동 분석" : `AI 질문 · ${userNames[String(t.userId)] ?? t.userId}`,
    status: TASK_STATUS[String(t.status)] ?? String(t.status),
    detail: String(t.answer ?? ""),
    userId: String(t.userId ?? ""),
    trigger: String(t.trigger ?? ""),
    ...(toTrace(t.trace) ? { trace: toTrace(t.trace) } : {}),
  }));
}
/** 협력사 평가지표(GET /api/suppliers/metrics) → 화면 평가지표 표 */
export function toMetrics(rows: Row[]): Workspace["metrics"] {
  return rows.map((m) => ({
    supplierId: String(m.supplier_id),
    itemId: String(m.item_id),
    supplier: String(m.supplier_name),
    item: String(m.item_name),
    onTime: Number(m.on_time_rate),
    quality: Number(m.quality_score),
    by: userNames[String(m.evaluated_by)] ?? String(m.evaluated_by),
    date: String(m.evaluated_at),
  }));
}
/**
 * 영향 분석 결과(analyze_impact 모양, contracts 2.2) → 화면 위험 목록.
 * 지금 발주하면 제때 오는 결품(orderable)은 위험이 아니라 "추가발주" 안내로 분류한다 (기획안 1.3).
 */
export function toIssues(impact: Row, items: Workspace["items"], plans: Workspace["plans"]): Issue[] {
  const name = new Map(items.map((i) => [i.id, i.name]));
  const plan = new Map(plans.map((p) => [p.id, p.product]));
  const where = (r: Row) => (r.plan_id ? `${r.plan_id} · ${plan.get(String(r.plan_id)) ?? ""}` : "");
  const list = (k: string) => ((impact[k] as Row[] | undefined) ?? []);
  return [
    ...list("shortages").map((r): Issue => ({
      kind: r.orderable ? "추가발주" : "결품",
      item: name.get(String(r.item_id)) ?? String(r.item_id),
      plan: where(r),
      date: String(r.plan_date),
      qty: Number(r.qty),
      detail: r.orderable
        ? `예상재고 ${r.projected_stock} · 지금 발주하면 제때 도착 (정상 발주 안내)`
        : `예상재고 ${r.projected_stock} · 새 발주로는 늦음 → 기존 발주 조정 필요 (${r.rule_id})`,
    })),
    ...list("warnings").map((r): Issue => ({
      kind: "주의",
      item: name.get(String(r.item_id)) ?? String(r.item_id),
      plan: where(r),
      date: String(r.plan_date),
      qty: Number(r.threshold) - Number(r.projected_stock),
      detail: `예상재고 ${r.projected_stock} < 안전재고 ${r.threshold} (${r.rule_id})`,
    })),
    ...list("excesses").map((r): Issue => ({
      kind: "과잉",
      item: name.get(String(r.item_id)) ?? String(r.item_id),
      plan: "",
      date: String(r.date),
      qty: Number(r.qty),
      detail: `앞으로 ${r.window_days}일 소요를 넘는 재고${r.amount != null ? ` · ${Number(r.amount).toLocaleString()}원` : ""} (${r.rule_id})`,
    })),
  ];
}
const OPTION_ACTION: Record<string, Option["action"]> = {
  SPLIT_PO: "split",
  DUE_DATE_CHANGE: "due-date",
  CHANGE_QTY: "qty",
  CANCEL_PO: "cancel",
};
/**
 * 해결안 비교 결과(compare_options, contracts 2.3) → 화면 해결안.
 * API 모드에는 사람의 검수·우선순위 단계가 없다 — 등급(cost_level)과 추천은 시스템이 계산한 값이라 "확정"으로 보여 주고,
 * 추천안이 1순위다. 금액은 backend가 역할대로 이미 가렸다 (생산관리는 등급만).
 */
export function toOptions(res: Row, role: Role, items: Workspace["items"], purchases: Workspace["purchases"]): Workspace["options"] {
  const name = new Map(items.map((i) => [i.id, i.name]));
  const poQty = new Map(purchases.map((p) => [p.id, p.qty]));
  return ((res.options as Row[] | undefined) ?? []).map((o) => {
    const args = (o.args ?? {}) as Row;
    const side = (o.side_effects as Row[] | undefined) ?? [];
    const left = (o.remaining_issues as Row[] | undefined) ?? [];
    const impact = side.length
      ? "다른 생산에 결품: " + side.map((x) => `${name.get(String(x.item_id)) ?? x.item_id} ${x.plan_id} +${x.increase_qty}개`).join(", ")
      : left.length
        ? "남는 문제: " + left.map((x) => `${name.get(String(x.item_id)) ?? x.item_id} ${x.plan_id ?? x.date} ${x.shortage_qty ?? x.excess_qty}개`).join(", ")
        : `${o.target_rule === "INV-003" ? "과잉" : "결품"} 해소 · 다른 생산 영향 없음`;
    const base = {
      action: OPTION_ACTION[String(o.type)] ?? "reallocate",
      id: String(o.option_id),
      revision: 1,
      name: String(o.summary),
      item: name.get(String(o.item_id)) ?? String(o.item_id),
      po: String(args.po_id ?? ""),
      qty: Number(args.split_qty ?? args.new_qty ?? poQty.get(String(args.po_id)) ?? 0),
      date: String(args.new_due_date ?? ""),
      resolves: Boolean(o.resolves) && side.length === 0,
      verification: "VERIFIED" as const,
      impact,
      risk: `납기 위험 ${o.risk}`,
      confirmed: false,
      review: "확정" as const,
      reviewRevision: 1,
      grade: o.cost_level as Grade,
      priority: o.recommended ? 1 : undefined,
      reason: o.recommended ? "시스템 추천 (결품 해소 → 부작용 없음 → 위험 → 비용 순)" : "",
    };
    const evidence = o.saved_amount != null
      ? `덜 사는 금액 ${Number(o.saved_amount).toLocaleString()}원`
      : "협력사 평가지표의 긴급 비용·분할 수수료로 계산 (POL-COST-EVAL 2.1)";
    if (role === "PLANNER") return { ...base, role };
    if (role === "BUYER") return { ...base, role, addedCost: Number(o.added_cost ?? 0), costEvidence: evidence };
    return { ...base, role, addedCost: Number(o.added_cost ?? 0), costEvidence: evidence, unitPrice: 0 };
  });
}
const api: Gateway = {
  async load(s) {
    const [rp, ri, rpo] = await Promise.all([
      request<{ plans: Row[] }>("/plans", s.token),
      request<{ items: Row[] }>("/inventory", s.token),
      request<{ purchase_orders: Row[] }>("/purchase-orders", s.token),
    ]);
    const { plans, items, purchases } = toWorkspace(rp, ri, rpo);
    // 승인 대기함: 팀장은 전부, 다른 역할은 자기가 요청한 카드만 — backend가 거른다 (decisions 6.28)
    const [cards, tasks, metrics, impact, compared] = await Promise.all([
      request<Row[]>("/approvals", s.token),
      request<Row[]>("/ai/tasks", s.token),
      request<{ metrics: Row[] }>("/suppliers/metrics", s.token),
      // 지금 DB 상태의 결품·과잉과 해결안. 일정 변경·지연 입력을 저장하면 다시 불러와 바로 반영된다
      request<Row>("/analysis/impact", s.token, "POST", { whatif: [] }),
      request<Row>("/analysis/options", s.token, "POST", { whatif: [] }),
    ]);
    const { approvals, options } = toApprovals(
      cards,
      new Map(ri.items.map((i) => [String(i.item_id), String(i.name)])),
    );
    return {
      plans,
      items,
      purchases,
      issues: toIssues(impact, items, plans),
      policyVersion: String(impact.policy_version ?? ""),
      // 승인 카드에서 만든 대안(승인함용) + 지금 상태의 해결안(해결안 비교용)
      options: [...toOptions(compared, s.role, items, purchases), ...options],
      approvals,
      history: toHistory(tasks),
      metrics: toMetrics(metrics.metrics),
    };
  },
  async save(s, c) {
    if (c.type === "plan")
      await request(`/plans/${c.id}`, s.token, "PUT", {
        date: c.date,
        qty: c.qty,
      });
    else if (c.type === "delay")
      await request(`/purchase-orders/${c.id}/due-date`, s.token, "PUT", {
        due_date: c.date,
        reason: c.reason,
      });
    else if (c.type === "metric") {
      // 평가지표 입력은 사람만 한다 (AI 도구로 열지 않음, decisions 2.4). 표의 몇 번째 줄인지로 협력사·부품을 찾는다
      const m = (await this.load(s)).metrics[c.index];
      if (!m?.supplierId) throw new Error("평가지표를 찾지 못했습니다. 새로고침 후 다시 시도하세요.");
      await request(`/suppliers/${m.supplierId}/items/${m.itemId}/metrics`, s.token, "PUT", {
        on_time_rate: c.onTime,
        quality_score: c.quality,
      });
    }
    else if (c.type === "request") {
      // 화면에는 승인 카드를 바로 만드는 API가 없다 — 승인 요청은 AI 작업이 검증(verify)을 거쳐 올린다 (contracts 3.3).
      // 그래서 이 해결안으로 진행해 달라는 AI 작업을 만든다. 승인 카드는 팀장 승인함에 생긴다
      const o = (await this.load(s)).options.find((x) => x.id === c.id);
      if (!o) throw new Error("해결안을 찾지 못했습니다. 새로고침 후 다시 시도하세요.");
      // 질문에 해결안 문장(날짜·수량)을 넣지 않는다 — AI가 그걸 가정 변경으로 읽어 "이미 풀린 상태"로 분석한다.
      // 지금 DB 상태 그대로 풀게 하면 AI도 같은 계산(compare_options)의 추천안을 골라 승인 요청을 올린다
      await request("/ai/tasks", s.token, "POST", {
        question: `지금 생산계획 기준으로 부족한 ${o.item} 해결 방법 만들어 줘`,
      });
    }
    else if (c.type === "approval")
      // 팀장 결정 → backend가 저장하고 agent에 재개를 요청한다 (contracts 3.3)
      await request(
        `/ai/tasks/${encodeURIComponent(approvalTasks.get(c.id) ?? "")}/approval`,
        s.token,
        "POST",
        { approvalId: c.id, decision: c.decision, reason: c.reason || null },
      );
    else
      throw new Error(
        "이 업무의 화면 DTO/API 계약은 아직 연결되지 않았습니다.",
      );
    return this.load(s);
  },
  reset() {},
};
export const gateway = mode === "mock" ? mock : api;
/** Approval polling does not trigger workspace-wide impact/option recomputation. */
export async function fetchApprovals(s: Session, items: Workspace["items"]) {
  const cards = await request<Row[]>("/approvals", s.token);
  return toApprovals(cards, new Map(items.map((i) => [i.id, i.name])));
}
export async function decideApproval(s: Session, taskId: string, id: string,
  decision: "APPROVED" | "REJECTED", reason: string) {
  await request(`/ai/tasks/${encodeURIComponent(taskId)}/approval`, s.token, "POST",
    { approvalId: id, decision, reason: reason || null });
}
/** 요청자의 초안 검토: 보내기(문제 정의·메모와 함께) 또는 보내지 않기 (decisions 6.33) */
export async function reviewApproval(s: Session, id: string, send: boolean, problem: string, memo: string) {
  await request(`/approvals/${encodeURIComponent(id)}/${send ? "submit" : "withdraw"}`, s.token, "POST",
    { problem, memo: memo || null });
}
export async function login(
  username: string,
  password: string,
): Promise<Session> {
  const r = await request<{ token: string; role: Role; name: string }>(
    "/auth/login",
    undefined,
    "POST",
    // [로컬 통합 임시] backend Auth는 user_id를 읽는다 (contracts 3.1). 칸 이름은 A·B 합의 필요
    { user_id: username, password },
  );
  if (!r.token || !["PLANNER", "BUYER", "APPROVER"].includes(r.role))
    throw new Error("로그인 응답 DTO 계약을 확인해주세요.");
  return { ...r, userId: username };
}
