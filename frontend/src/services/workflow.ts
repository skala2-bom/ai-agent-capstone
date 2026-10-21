import type { Command, Workspace, Session, Option } from "../types";
export function selectable(o: Option) {
  return (
    o.review === "확정" &&
    o.reviewRevision === o.revision &&
    o.resolves &&
    o.verification !== "PENDING"
  );
}
export function mutate(w: Workspace, c: Command, s: Session) {
  const buyer = s.role !== "PLANNER";
  const o = "id" in c ? w.options.find((x) => x.id === c.id) : undefined;
  const require = (ok: boolean, message: string) => {
    if (!ok) throw new Error(message);
  };
  if (c.type === "plan") {
    require(s.role === "PLANNER", "생산관리 권한이 필요합니다.");
    require(Number.isInteger(c.qty) &&
      c.qty > 0 &&
      !!c.date, "유효한 날짜와 수량을 입력하세요.");
    Object.assign(
      w.plans.find((p) => p.id === c.id)!,
      { date: c.date, qty: c.qty },
    );
  }
  if (c.type === "delay") {
    const po = w.purchases.find((p) => p.id === c.id);
    require(!!po &&
      po.received === 0, "실제 입고된 발주의 예정일은 변경할 수 없습니다.");
    require(/^\d{4}-\d{2}-\d{2}$/.test(c.date) &&
      c.date >= po!.date, "현재 예정일 이후의 날짜를 입력하세요.");
    require(buyer && !!c.reason.trim(), "구매 권한과 변경 사유가 필요합니다.");
    Object.assign(
      w.purchases.find((p) => p.id === c.id)!,
      { date: c.date },
    );
  }
  if (c.type === "purchase") {
    const po = w.purchases.find(p => p.id === c.id);
    require(buyer, "구매 권한이 필요합니다.");
    require(!!po && po.received === 0 && ["OPEN", "입고 예정", "지연 통보"].includes(po.status), "입고 예정인 발주만 수정할 수 있습니다.");
    require(Number.isInteger(c.qty) && c.qty > 0 && c.qty <= 2147483647, "수량은 1 이상의 정수여야 합니다.");
    require(/^\d{4}-\d{2}-\d{2}$/.test(c.date) && c.date >= "2026-11-02", "기준일 이후의 예정일을 입력하세요.");
    require(!!c.reason.trim(), "변경 사유를 입력하세요.");
    require(po!.qty !== c.qty || po!.date !== c.date, "변경된 내용이 없습니다.");
    Object.assign(po!, { qty: c.qty, date: c.date });
  }
  if (c.type === "review" || c.type === "priority")
    require(!w.approvals.some(
      (a) => a.optionId === c.id && a.status !== "REJECTED",
    ), "승인 요청된 후보는 수정할 수 없습니다. 별도 버전의 후보를 만들어주세요.");
  if (c.type === "review") {
    require(buyer && !!o, "구매 권한이 필요합니다.");
    require(!!c.note.trim(), "검수 근거를 입력하세요.");
    Object.assign(o!, {
      grade: c.confirm ? c.grade : undefined,
      review: c.confirm ? "확정" : "보완 요청",
      reviewRevision: o!.revision,
      note: c.note,
      priority: undefined,
    });
  }
  if (c.type === "priority") {
    require(s.role === "PLANNER" &&
      !!o &&
      selectable(
        o,
      ), "현재 버전 검수가 확정되고 결품이 해소되는 안만 선정할 수 있습니다.");
    require(!!c.reason.trim() &&
      c.priority > 0, "선택 사유와 우선순위를 입력하세요.");
    w.options.forEach((x) => {
      if (x.priority === c.priority) x.priority = undefined;
    });
    Object.assign(o!, { priority: c.priority, reason: c.reason });
  }
  if (c.type === "request") {
    require(o?.action !==
      "plan", "생산일 조정안은 생산계획 화면에서 저장하세요. 발주 변경 대상이 아닙니다.");
    require(buyer &&
      !!o &&
      selectable(o) &&
      o.priority === 1, "생산관리 1순위로 선정된 유효한 해결안이 필요합니다.");
    require(!w.approvals.some(
      (a) => a.optionId === c.id && a.status !== "REJECTED",
    ), "이미 요청한 해결안입니다.");
    w.approvals.push({
      id: crypto.randomUUID(),
      taskId: `demo-task-${w.environment ?? "SC1"}`,
      beforeQty: w.purchases.find((p) => p.id === o!.po)?.qty,
      beforeDate: w.purchases.find((p) => p.id === o!.po)?.date,
      optionId: c.id,
      requester: s.name,
      at: new Date().toISOString(),
      status: "PENDING",
    });
  }
  if (c.type === "approval") {
    const a = w.approvals.find((x) => x.id === c.id)!;
    require(s.role === "APPROVER" &&
      a.status === "PENDING", "승인 가능한 대기 요청이 아닙니다.");
    require(c.decision !== "REJECTED" ||
      !!c.reason.trim(), "거절 사유가 필요합니다.");
    a.status = c.decision;
    a.reason = c.reason;
    if (c.decision === "REJECTED") {
      const old = w.options.find((x) => x.id === a.optionId)!;
      old.priority = undefined;
      const next = {
        ...old,
        id: `OPT-R${w.options.length}`,
        revision: old.revision + 1,
        name: `보완 대안 · ${old.name}`,
        review: "미검수" as const,
        grade: undefined,
        reviewRevision: undefined,
        priority: undefined,
        note: c.reason,
      };
      w.options.push(next);
    }
  }
  if (c.type === "execution") {
    const a = w.approvals.find((x) => x.id === c.id)!;
    require(s.role === "APPROVER" &&
      a.status ===
        "APPROVED", "승인 접수 이후에만 실행 이벤트를 확인할 수 있습니다.");
    const opt = w.options.find((x) => x.id === a.optionId)!;
    if (opt.action === "split") {
      const po = w.purchases.find((x) => x.id === opt.po)!;
      require(po.received === 0 &&
        opt.qty > 0 &&
        opt.qty < po.qty, "분할 가능한 미입고 잔량을 확인하세요.");
      require(a.beforeQty === undefined ||
        (a.beforeQty === po.qty &&
          a.beforeDate ===
            po.date), "승인 이후 원 발주가 변경되었습니다. 다시 요청하세요.");
      po.qty -= opt.qty;
      w.purchases.push({
        ...po,
        id: `${po.id}-S1`,
        qty: opt.qty,
        date: opt.date,
      });
    } else if (opt.action === "new-po") {
      w.purchases.push({
        id: `PO-DEMO-${w.purchases.length + 1}`,
        item: opt.item,
        supplier: "대체 협력사",
        qty: opt.qty,
        date: opt.date,
        received: 0,
        passed: 0,
        failed: 0,
        status: "입고 예정",
      });
    } else throw new Error("이 해결안의 실행은 별도 업무 API가 필요합니다.");
    a.status = "EXECUTED";
  }
  if (c.type === "metric") {
    require(buyer, "구매 권한이 필요합니다.");
    require([c.onTime, c.quality].every(
      (n) => Number.isFinite(n) && n >= 0 && n <= 100,
    ), "0~100 범위를 입력하세요.");
    Object.assign(w.metrics[c.index], {
      onTime: c.onTime,
      quality: c.quality,
      by: s.name,
    });
  }
  if (c.type === "receipt") {
    const p = w.purchases.find((x) => x.id === c.id)!;
    require(buyer && p.received > 0, "실제 입고된 발주만 검수할 수 있습니다.");
    require([c.passed, c.failed].every((n) => Number.isInteger(n) && n >= 0) &&
      c.passed + c.failed ===
        p.received, "합격·불합격 합계는 실제 입고량과 같아야 합니다.");
    Object.assign(p, {
      passed: c.passed,
      failed: c.failed,
      status: "검수 완료",
    });
  }
  if (["plan", "delay", "purchase", "execution"].includes(c.type)) w.analysisStale = true;
  w.history.unshift({
    id: crypto.randomUUID(),
    at: new Date().toISOString(),
    title: {
      plan: "생산계획 저장",
      delay: "입고 지연 통보",
      purchase: "입고 정보 수동 변경",
      review: "비용등급 검수",
      priority: "생산 우선순위 저장",
      request: "발주 변경 승인 요청",
      approval: "승인 결정 접수",
      execution: "실행 완료 이벤트 확인",
      metric: "협력사 평가지표 저장",
      receipt: "입고 검수 완료",
    }[c.type],
    target:
      "id" in c
        ? (w.plans.find((p) => p.id === c.id)?.product ??
          w.options.find((o) => o.id === c.id)?.name ??
          w.purchases.find((p) => p.id === c.id)?.item ??
          "승인 요청")
        : "협력사 평가지표",
    status:
      c.type === "request"
        ? "승인 대기"
        : c.type === "approval"
          ? c.decision === "REJECTED"
            ? "재검토"
            : "실행 대기"
          : "완료",
    detail: s.name,
    userId: s.userId ?? s.role.toLowerCase(),
  });
}
