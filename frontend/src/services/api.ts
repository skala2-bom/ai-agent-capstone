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
const api: Gateway = {
  async load(s) {
    const [plans, items, purchases] = await Promise.all([
      request<Workspace["plans"]>("/plans", s.token),
      request<Workspace["items"]>("/inventory", s.token),
      request<Workspace["purchases"]>("/purchase-orders", s.token),
    ]);
    return {
      plans,
      items,
      purchases,
      options: [],
      approvals: [],
      history: [],
      metrics: [],
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
    { username, password },
  );
  if (!r.token || !["PLANNER", "BUYER", "APPROVER"].includes(r.role))
    throw new Error("로그인 응답 DTO 계약을 확인해주세요.");
  return r;
}
