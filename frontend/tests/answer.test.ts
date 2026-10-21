import { describe, expect, it } from "vitest";
import { segments, sourceCards } from "../src/services/answer";

describe("답변 블록 나누기", () => {
  it("결론·제목·항목으로 나누고 마크다운 기호를 지운다", () => {
    const s = segments(
      "**스위치 생산**이 위험합니다.\n\n연쇄 결품: 바꾸지 않은 PP-06도 모자랍니다.\n\n결품 2건:\n- 스위치 생산(PP-03, 2026-11-23): 릴레이 400개 부족 · 새 발주로는 늦음, 기존 발주 조정 필요\n* 플러그 생산(PP-04, 2026-11-30): 릴레이 100개 부족 · 지금 발주하면 제때 도착\n\n실행한 변경:\n- PO-1는 400개 · 12/12 입고로 줄었습니다\n\n## 참고\n1. `INV-003` 과잉",
    );
    expect(s).toEqual([
      { kind: "lead", text: "스위치 생산이 위험합니다." },
      { kind: "callout", text: "연쇄 결품: 바꾸지 않은 PP-06도 모자랍니다." },
      { kind: "heading", text: "결품 2건", tone: "danger" },
      {
        kind: "list",
        items: [
          { where: "스위치 생산(PP-03, 2026-11-23)", what: "릴레이 400개 부족", note: "새 발주로는 늦음, 기존 발주 조정 필요", tone: "danger" },
          { where: "플러그 생산(PP-04, 2026-11-30)", what: "릴레이 100개 부족", note: "지금 발주하면 제때 도착", tone: "success" },
        ],
      },
      { kind: "heading", text: "실행한 변경", tone: "success" },
      // 결품 구역이 아니면 " · "로 자르지 않는다
      { kind: "list", items: [{ what: "PO-1는 400개 · 12/12 입고로 줄었습니다", where: undefined, note: undefined, tone: undefined }] },
      { kind: "text", text: "참고" },
      { kind: "list", items: [{ what: "INV-003 과잉", where: undefined, note: undefined, tone: undefined }] },
    ]);
  });

  it("근거는 같은 조항을 한 번만", () => {
    const a = { uri: "doc://ECN-2026-014/1.1", title: "설계변경" };
    expect(sourceCards([a, a, null, { uri: "doc://CT-SUP01/3.1" }])).toHaveLength(2);
    expect(sourceCards(undefined)).toEqual([]);
  });
});
