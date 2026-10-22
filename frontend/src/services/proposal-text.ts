// 해결안 문장(agent의 summary)을 화면용으로 줄인다: 핵심 한 줄 + 발주 정보 + 작은 표시.
// summary는 backend OptionAnalysis·agent가 정해진 모양으로 만든다 (decisions 6.47). 모양이 다르면 원문을 그대로 쓴다.
// ponytail: 문장을 읽어 나눈다. 카드에 칸(buffer·revision)이 생기면 그 칸을 읽도록 바꾼다

export interface ProposalText {
  revision?: number;      // 재제안 n차 (최초 제안이면 없음)
  main: string;           // 무엇을 하나: "MCU 150개를 11/22에 먼저 받기"
  po?: string;            // 어느 발주에서: "PO-2609-001 · 12/12 입고 500개에서"
  tags: string[];         // "3일 여유", "안전재고 +50", "직접 수정"
}

const HEAD = /^(PO-[\w-]+)\((.+?) ([\d,]+)개, (\d{1,2}\/\d{1,2}) 입고\)/;

export function proposalText(text = ""): ProposalText {
  let s = text.replace(/\s*\(실제 발주 변경입니다\.\s*협력사 협의·확약 여부를 확인한 뒤 승인하세요\.\)/g, "").trim();
  const out: ProposalText = { main: s, tags: [] };
  const rev = s.match(/^\[재제안 (\d+)차 · 이전 요청 [^\]]+\]\s*/);
  if (rev) { out.revision = Number(rev[1]); s = s.slice(rev[0].length); }
  const manual = s.match(/^\[직접 수정\] (PO-[\w-]+) 수량 ([\d,]+)개 · 입고일 (\S+)/);
  if (manual) return { ...out, main: `수량 ${manual[2]}개 · 입고일 ${manual[3]}`, po: manual[1], tags: ["직접 수정"] };
  const buffer = s.match(/\s*\(((?:결품일보다 \d+일 일찍|안전재고분 [\d,]+개 더)(?:, (?:결품일보다 \d+일 일찍|안전재고분 [\d,]+개 더))*)\)$/);
  if (buffer) {
    s = s.slice(0, buffer.index);
    for (const part of buffer[1].split(", ")) {
      const early = part.match(/(\d+)일 일찍/), safety = part.match(/안전재고분 ([\d,]+)개/);
      out.tags.push(early ? `${early[1]}일 여유` : `안전재고 +${safety![1]}`);
    }
  }
  const head = s.match(HEAD);
  if (!head) return { ...out, main: s };
  const [, po, item, qty, due] = head, rest = s.slice(head[0].length);
  const from = `${po} · ${due} 입고 ${qty}개`;
  let m: RegExpMatchArray | null;
  if ((m = rest.match(/^에서 ([\d,]+)개만 떼어 (\S+)에 먼저 받는다/))) return { ...out, main: `${item} ${m[1]}개를 ${m[2]}에 먼저 받기`, po: `${from}에서 나눔` };
  if ((m = rest.match(/^ 전량을 (\S+)에 받는다/))) return { ...out, main: `${item} ${qty}개 전량을 ${m[1]}에 받기`, po: from };
  if ((m = rest.match(/^을 ([\d,]+)개로 줄인다 \(([\d,]+)개 덜 산다\)/))) return { ...out, main: `${item} ${m[1]}개로 줄이기 (${m[2]}개 덜 사기)`, po: from };
  if (/^을 취소한다/.test(rest)) return { ...out, main: `${item} ${qty}개 발주 취소`, po: from };
  if ((m = rest.match(/^ 입고를 다음 사용일 (\S+)로 미룬다/))) return { ...out, main: `${item} 입고를 ${m[1]}로 미루기`, po: from };
  return { ...out, main: s };
}
