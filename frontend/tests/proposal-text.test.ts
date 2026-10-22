import { describe, expect, it } from "vitest";
import { proposalText } from "../src/services/proposal-text";

const tail = " (실제 발주 변경입니다. 협력사 협의·확약 여부를 확인한 뒤 승인하세요.)";

describe("해결안 제목 줄이기 (decisions 6.46)", () => {
  it("재제안·분할·여유 표시를 나눈다", () => {
    expect(proposalText("[재제안 2차 · 이전 요청 APR-71534f35] PO-2609-001(MCU 500개, 12/12 입고)에서 150개만 떼어 11/22에 먼저 받는다 (결품일보다 3일 일찍, 안전재고분 50개 더)" + tail))
      .toEqual({ revision: 2, main: "MCU 150개를 11/22에 먼저 받기", po: "PO-2609-001 · 12/12 입고 500개에서 나눔", tags: ["3일 여유", "안전재고 +50"] });
  });
  it("전량 당기기·감량·취소·미루기·직접 수정", () => {
    expect(proposalText("PO-2610-106(릴레이 1,200개, 11/20 입고) 전량을 11/23에 받는다 (안전재고분 200개 더)").main).toBe("릴레이 1,200개 전량을 11/23에 받기");
    expect(proposalText("PO-2610-115(플러그 케이스 800개, 11/27 입고)을 500개로 줄인다 (300개 덜 산다)").main).toBe("플러그 케이스 500개로 줄이기 (300개 덜 사기)");
    expect(proposalText("PO-2610-110(옛 케이스 800개, 11/15 입고)을 취소한다").main).toBe("옛 케이스 800개 발주 취소");
    expect(proposalText("PO-2610-103(코인전지 800개, 11/15 입고) 입고를 다음 사용일 1/4로 미룬다").main).toBe("코인전지 입고를 1/4로 미루기");
    expect(proposalText("[재제안 3차 · 이전 요청 APR-1] [직접 수정] PO-2609-001 수량 500개 · 입고일 2026-11-25"))
      .toEqual({ revision: 3, main: "수량 500개 · 입고일 2026-11-25", po: "PO-2609-001", tags: ["직접 수정"] });
  });
  it("모양이 다르면 원문을 그대로 쓴다", () => {
    expect(proposalText("알 수 없는 문장" + tail)).toEqual({ main: "알 수 없는 문장", tags: [] });
  });
});
