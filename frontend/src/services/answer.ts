// AI 답변(평문)을 화면 블록으로 나눈다 (decisions 6.27).
// agent는 "결론 한 줄 → 빈 줄 → '제목 n건:' + '- 항목' 줄"로 쓴다. 모델이 지시를 어기고 마크다운을 섞어도
// 기호(**, #, `)는 지워서 글자 그대로 보이지 않게 한다. HTML로 넣지 않으니 답변 안의 태그도 글자로만 보인다.
export type Tone = "danger" | "warning" | "success" | undefined;
export interface Item {
  where?: string; // "스마트 벽스위치 생산(PP-03, 2026-11-23)" — 작게, 위에
  what: string; // "릴레이 400개 부족" — 굵게
  note?: string; // "새 발주로는 늦음, 기존 발주 조정 필요" — 색 꼬리표
  tone?: Tone;
}
export type Segment =
  | { kind: "lead" | "text" | "callout"; text: string }
  | { kind: "heading"; text: string; tone: Tone }
  | { kind: "list"; items: Item[] };

const BULLET = /^\s*(?:[-•*]|\d+[.)])\s+/;

function clean(line: string) {
  return line
    .replace(/\*\*|__|`/g, "")
    .replace(/^\s*#+\s*/, "")
    .trim();
}

// 색은 뜻으로만 고른다: 빨강 = 사람이 손써야 함, 노랑 = 지켜볼 것, 초록 = 괜찮음
function headingTone(t: string): Tone {
  if (/^(결품|보류|거절)/.test(t)) return "danger";
  if (/^(주의|과잉)/.test(t)) return "warning";
  if (/^실행한 변경/.test(t)) return "success";
}
function noteTone(t: string): Tone {
  if (/늦음|조정 필요|실패|보류/.test(t)) return "danger";
  if (/제때|완료|해소/.test(t)) return "success";
}

function item(text: string, tagged: boolean): Item {
  // 꼬리표 = 마지막 " · " 뒤 — 결품·주의·과잉 구역에서만 (실행 결과 문장은 " · "가 문장 중간에 있다).
  // 위치 = 첫 ": " 앞 (계획·문서 이름)
  const dot = tagged ? text.lastIndexOf(" · ") : -1;
  const note = dot > 0 ? text.slice(dot + 3) : undefined;
  const body = dot > 0 ? text.slice(0, dot) : text;
  const colon = body.indexOf(": ");
  const where = colon > 0 && colon <= 45 ? body.slice(0, colon) : undefined;
  const what = where ? body.slice(colon + 2) : body;
  return { where, what, note, tone: note ? noteTone(note) : undefined };
}

export function segments(answer: string): Segment[] {
  const out: Segment[] = [];
  let tagged = false;
  for (const raw of answer.split("\n")) {
    if (!raw.trim()) continue;
    if (BULLET.test(raw)) {
      const it = item(clean(raw.replace(BULLET, "")), tagged);
      const last = out[out.length - 1];
      if (last?.kind === "list") last.items.push(it);
      else out.push({ kind: "list", items: [it] });
      continue;
    }
    const text = clean(raw);
    if (!text) continue;
    // 짧고 콜론으로 끝나면 구역 제목 ("결품 2건:", "확인한 근거:")
    if (/[:：]$/.test(text) && text.length <= 30) {
      const t = text.slice(0, -1);
      tagged = /^(결품|주의|과잉)/.test(t);
      out.push({ kind: "heading", text: t, tone: headingTone(t) });
    } else if (text.startsWith("연쇄 결품:")) out.push({ kind: "callout", text });
    else out.push({ kind: out.length ? "text" : "lead", text });
  }
  return out;
}

export interface Source {
  doc_id?: string;
  title?: string;
  clause_no?: string;
  version?: string;
  effective_date?: string;
  uri?: string;
  excerpt?: string;
}

/** DONE.sources → 화면 카드. 같은 조항이 여러 번 오면 하나만 */
export function sourceCards(sources: unknown): Source[] {
  if (!Array.isArray(sources)) return [];
  const seen = new Set<string>();
  return sources.filter((s): s is Source => {
    if (!s || typeof s !== "object") return false;
    const key = (s as Source).uri ?? JSON.stringify(s);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
