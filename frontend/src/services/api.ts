import type { Command, Session, Workspace, Role } from "../types";
import { environmentFixture } from "../mocks/environments";
import { mutate } from "./workflow";
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
    const split = c.tool === "split_po";
    approvalTasks.set(id, String(c.task_id));
    approvals.push({
      id,
      taskId: String(c.task_id),
      optionId: id,
      requester: userNames[String(c.requested_by)] ?? String(c.requested_by),
      at: String(c.requested_at),
      status: c.status as Workspace["approvals"][number]["status"],
      reason: (c.reason as string) ?? undefined,
      beforeQty: Number(before.qty),
      beforeDate: String(before.due_date),
    });
    options.push({
      action: split ? "split" : "due-date",
      id,
      revision: 1,
      name: String(c.summary ?? c.tool),
      item: names.get(String(before.item_id)) ?? String(before.item_id),
      po: String(args.po_id),
      qty: split ? Number(args.split_qty) : Number(before.qty),
      date: String(args.new_due_date),
      resolves: true,
      verification: "VERIFIED",
      impact: split ? "" : `입고 예정일 ${before.due_date} → ${args.new_due_date}`,
      risk: "",
      confirmed: true,
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
const api: Gateway = {
  async load(s) {
    const [rp, ri, rpo] = await Promise.all([
      request<{ plans: Row[] }>("/plans", s.token),
      request<{ items: Row[] }>("/inventory", s.token),
      request<{ purchase_orders: Row[] }>("/purchase-orders", s.token),
    ]);
    const { plans, items, purchases } = toWorkspace(rp, ri, rpo);
    // 승인 대기함은 팀장만 볼 수 있다 (contracts 3.1). 다른 역할은 빈 목록
    const [cards, tasks, metrics] = await Promise.all([
      s.role === "APPROVER" ? request<Row[]>("/approvals", s.token) : Promise.resolve([] as Row[]),
      request<Row[]>("/ai/tasks", s.token),
      request<{ metrics: Row[] }>("/suppliers/metrics", s.token),
    ]);
    const { approvals, options } = toApprovals(
      cards,
      new Map(ri.items.map((i) => [String(i.item_id), String(i.name)])),
    );
    return {
      plans,
      items,
      purchases,
      options,
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
  return r;
}
