export type Role = "PLANNER" | "BUYER" | "APPROVER";
// A~D = 목데이터 모드의 구매 검수 등급, LOW·MID·HIGH = API 모드의 시스템 비용 등급 (cost_level, decisions 6.15)
export type Grade = "A" | "B" | "C" | "D" | "LOW" | "MID" | "HIGH";
export interface Plan {
  id: string;
  product: string;
  date: string;
  qty: number;
  status: string;
}
export interface Item {
  id: string;
  name: string;
  type: "FG" | "PART";
  stock: number;
  safety: number;
  supplier: string;
  lead: number;
  shared: string[];
}
export interface Purchase {
  id: string;
  item: string;
  supplier: string;
  qty: number;
  date: string;
  received: number;
  passed: number;
  failed: number;
  status: string;
}
export interface Issue {
  kind: "결품" | "주의" | "과잉" | "추가발주" | "사용 제한";
  item: string;
  plan: string;
  date: string;
  qty: number;
  detail: string;
}
export interface Scenario {
  id: string;
  name: string;
  change: string;
  policy_version: string;
  issues: Issue[];
}
export interface Option {
  action: "split" | "plan" | "new-po" | "reallocate" | "due-date" | "qty" | "cancel";
  id: string;
  revision: number;
  name: string;
  item: string;
  po: string;
  qty: number;
  date: string;
  resolves: boolean;
  verification?: "PENDING" | "VERIFIED";
  impact: string;
  risk: string;
  confirmed: boolean;
  review: "미검수" | "확정" | "보완 요청" | "대체됨";
  reviewRevision?: number;
  grade?: Grade;
  priority?: number;
  reason?: string;
  note?: string;
}
export type PlannerOption = Option & { role: "PLANNER" };
export type BuyerOption = Option & {
  role: "BUYER";
  addedCost: number;
  costEvidence: string;
};
export type ApproverOption = Option & {
  role: "APPROVER";
  addedCost: number;
  costEvidence: string;
  unitPrice: number;
};
export type OptionView = PlannerOption | BuyerOption | ApproverOption;
export interface Approval {
  id: string;
  taskId: string;
  optionId: string;
  requester: string;
  at: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXECUTED";
  reason?: string;
  beforeQty?: number;
  beforeDate?: string;
}
export interface History {
  id: string;
  at: string;
  title: string;
  target: string;
  status: string;
  detail: string;
}
export interface Metric {
  supplierId?: string; // API 모드에서 저장할 때 쓰는 ID (화면에는 이름을 보여 준다)
  itemId?: string;
  supplier: string;
  item: string;
  onTime: number;
  quality: number;
  by: string;
  date: string;
}
export interface Workspace {
  environment?: string;
  issues?: Issue[];
  policyVersion?: string;
  analysisStale?: boolean;
  plans: Plan[];
  items: Item[];
  purchases: Purchase[];
  options: OptionView[];
  approvals: Approval[];
  history: History[];
  metrics: Metric[];
}
export interface Session {
  role: Role;
  name: string;
  token?: string;
}
export type Command =
  | { type: "plan"; id: string; date: string; qty: number }
  | { type: "delay"; id: string; date: string; reason: string }
  | { type: "review"; id: string; grade: Grade; note: string; confirm: boolean }
  | { type: "priority"; id: string; priority: number; reason: string }
  | { type: "request"; id: string }
  | {
      type: "approval";
      id: string;
      decision: "APPROVED" | "REJECTED";
      reason: string;
    }
  | { type: "execution"; id: string }
  | { type: "metric"; index: number; onTime: number; quality: number }
  | { type: "receipt"; id: string; passed: number; failed: number };
export interface AgentEvent {
  type:
    | "TOKEN"
    | "TOOL_START"
    | "TOOL_END"
    | "APPROVAL_REQUIRED"
    | "DONE"
    | "FAILED";
  taskId: string;
  seq: number;
  ts: string;
  text?: string;
  answer?: string;
  tool?: string;
  label?: string;
  ok?: boolean;
  approvalId?: string;
  summary?: string;
  message?: string;
  traceId?: string;
  result?: unknown;
  sources?: unknown;
}
