# 파트 간 계약

> **이 문서의 역할**: 파트 경계를 넘는 약속만 모았다. 여기 적힌 것은 **바꾸려면 상대 파트와 합의**해야 하고, 여기 없는 것은 각 파트가 알아서 정하면 된다.
>
> **정한 사람**: 기술 선택은 교재와 평가표 기준으로 Claude가 정했다. 왜 그렇게 정했는지는 [decisions.md 6절](decisions.md). 바꾸고 싶으면 그 근거를 보고 뒤집으면 된다.
>
> **갱신**: 2026-10-20 (오후 — `/internal` 1차 응답 모양·DB 접속 정보·seed·골든셋, `orderable` 칸 추가)

## 파트 구분

| 파트 | 범위 | 폴더 |
|---|---|---|
| **A. Frontend** | 화면 | `frontend/` |
| **B. Backend · DB** | 업무 API, 작업 API, SSE 중계, `/internal/**`, 인증, 계산, DB, seed | `backend/` |
| **C. AI · MCP · RAG** | Agent, Harness, business-mcp, rag-mcp, 색인 | `agent/`, `business-mcp/`, `rag-mcp/` |

---

## 0. 이미 굳은 것 — 다시 논의하지 않는다

뼈대가 코드로 고정했고 `docker compose up`으로 동작을 확인한 것들이다.

| 항목 | 값 |
|---|---|
| 작업 접수 | `POST /api/ai/tasks` → `202 {taskId}` |
| 진행 구독 | `GET /api/ai/tasks/{id}/stream` (SSE) |
| 이벤트 공통 필드 | `type`, `taskId`, `seq`, `ts` — backend가 붙인다 |
| 백엔드 → agent | `POST /agent/tasks` → `202` |
| agent → 백엔드 | `POST /internal/tasks/{id}/events` |
| MCP 연결 | Streamable HTTP, `business-mcp:9101/mcp` · `rag-mcp:9102/mcp` |
| 공개 포트 | frontend·backend만 |
| 호출 방향 | business-mcp는 DB를 직접 보지 않고 `/internal/**`만 부른다 |

---

## 1. 전 파트 공통

### 1.1 요청자 정보 헤더 — A·B·C 전부 지켜야 동작한다

금액 마스킹(SEC-001)을 하려면 backend가 **요청자의 역할**을 알아야 한다. 경로는 이렇다.

```
agent ──(헤더 4개)──▶ business-mcp ──(그대로 전달)──▶ backend /internal/**
```

| 헤더 | 값 | 넣는 쪽 |
|---|---|---|
| `X-Task-Id` | 작업 ID | agent |
| `X-User-Id` | 요청자 ID | agent (backend가 `/agent/tasks`로 넘겨 준 값) |
| `X-Role` | `PLANNER` \| `BUYER` \| `APPROVER` | agent |
| `X-Service-Token` | 서비스별 토큰 | 부르는 쪽마다 다른 값 |

- **역할을 도구 인자에 넣지 않는다.** 도구 인자는 모델이 채우는 값이라 조작될 수 있다.
- business-mcp는 받은 헤더를 **해석하지 않고 그대로** 전달한다.
- 나중에 붙이면 agent와 business-mcp를 동시에 고쳐야 하므로 **처음부터 넣는다.**

### 1.2 역할별 금액 공개 범위 (규칙 SEC-001)

| 역할 | 볼 수 있는 것 |
|---|---|
| `PLANNER` 생산관리 | 금액 비공개 |
| `BUYER` 구매 | 총액까지 |
| `APPROVER` 구매팀장 | 계약 단가까지 |

- 1차 차단은 **backend masking**이 `/internal/**` 응답에서 한다. 규칙은 여기 한 곳에만 둔다.
- 2차는 agent Harness가 도구 결과에 단가 필드가 남아 있으면 모델에 넘기기 전에 지운다.

### 1.3 DB 스키마와 계정

| 스키마 | 테이블 | 쓰는 파트 | 계정 |
|---|---|---|---|
| `public` | 업무·작업·승인 | B | `app` |
| `agent` | LangGraph checkpointer | C | `agent` |
| `rag` | `doc_chunk` | C | `rag` |

- **B가 `init.sql`로 스키마 3개와 계정 3개를 만든다.** 각 계정은 자기 스키마에만 권한을 갖는다.
- `agent` 계정에는 `CREATE` 권한을 준다. checkpointer 테이블은 LangGraph가 직접 만든다.
- 계정을 나누는 이유: 아키텍처 11장 보안성 증빙에 "DB 계정 분리"가 들어 있다. init SQL 몇 줄이면 된다.
- **접속 정보** (✅ [`db/init.sql`](../db/init.sql)에 구현): 호스트 `postgres:5432`, DB `bom`, 계정·비밀번호 `app/app` · `agent/agent` · `rag/rag`. 포트를 밖으로 열지 않은 내부망 개발값이다.
  - 계정마다 `search_path`가 자기 스키마라 테이블 이름에 스키마를 안 붙여도 된다.
  - **pgvector 확장은 `rag` 스키마에 있다.** `rag` 계정은 `vector(1536)`처럼 그냥 쓰면 된다. 다른 계정에는 이 타입이 보이지 않는다.
  - DB를 쓰기 시작하는 서비스는 `depends_on: postgres: condition: service_healthy`를 붙인다 (healthcheck 있음).
  - `db/` 안의 `init.sql` → `schema.sql` → `seed.sql`이 이름 순서로 돈다. **빈 볼륨일 때만** 돌므로 다시 돌리려면 `docker compose down -v`.

### 1.4 seed 데이터

- ✅ **B가 만들었다** ([`db/seed.sql`](../db/seed.sql)). 기획안의 완제품 4종·구매 부품 28종(공용 17종)·BOM 3단계·협력사 5곳(기획안 3 + 대체 2)·생산계획 7건·진행 중 발주 31건.
- 기획안과 다른 곳은 [decisions.md 7.1](decisions.md)에 전부 있다.
- **변경 전에는 문제가 0건이다.** SC1~SC4는 일정·입고일을 바꾸는 순간에만 걸린다.
- 시연 기준일은 **2026-11-02**. 로그인 계정 `planner` · `buyer` · `approver`, 비밀번호 `1234`.
- **ID는 영문**이다: 완제품 `FG-PLUG` · `FG-SWITCH` · `FG-DOOR` · `FG-HUB`, 부품 `MCU` · `WIFI-CHIP` · `RELAY` · `CASE-DOOR-V1` 등. 한글 이름은 `name`에 있다. 프롬프트·평가셋에 ID를 적을 때 이걸 쓴다.
- **골든셋 정답의 기준은 [`eval/golden.json`](../eval/golden.json)이다** (2.8). [decisions.md 7.3](decisions.md) 표는 설명용이다. 요약: SC1 MCU −100 2시점 / SC2 릴레이 −400 외 결품 5건·MCU 경고·플러그 케이스 과잉 300 / SC3 새 케이스 −800·옛 케이스 과잉 800·코인전지 과잉 800 / SC4 릴레이 −1,000. 기획안의 SC3 BLE 칩·홀 센서 과잉은 과잉 기간 규칙 때문에 빠졌다 (decisions.md 1.3). 값을 바꾸면 정답도 다시 계산해야 한다.

---

## 2. B ↔ C 계약 (backend ↔ AI·MCP)

### 2.1 `/internal/**` 엔드포인트 — 도구 하나당 API 하나

**business-mcp 도구 목록이 곧 B가 만들 API 목록이다.** C가 도구를 늘리면 B의 일이 늘어난다.

#### 1차 (데모 SC1~SC4를 도는 데 필요한 것) — 8개

| 도구 | Method · Path | 승인 |
|---|---|:-:|
| `get_item` | GET `/internal/items/{id}` | |
| `get_inventory` | GET `/internal/inventory?type=FG\|PART&item_id=` | |
| `list_plans` | GET `/internal/plans?from=&to=` | |
| `list_receipts` | GET `/internal/purchase-orders?status=OPEN&item_id=&from=&to=` | |
| `analyze_impact` | POST `/internal/analysis/impact` | |
| `compare_options` | POST `/internal/analysis/options` | |
| `request_due_date_change` | POST `/internal/purchase-orders/{id}/due-date` | ✅ |
| `split_po` | POST `/internal/purchase-orders/{id}/split` | ✅ |

#### 1차 응답 모양 (2.2·2.3 외 6개)

✅ **backend가 이 모양으로 응답한다** (`backend/.../InternalApi.java`). 조회 4개는 seed DB에서 읽고, 계산 2개는 아직 고정 응답(seed 기준 SC1 정답), 변경 2개는 검사만 하고 저장은 안 한다. 아래 예시는 실제 응답이다.
키는 snake_case, 수량은 개, 금액은 원, 날짜는 `YYYY-MM-DD`.

```jsonc
// get_item  GET /internal/items/MCU
{ "item_id": "MCU", "name": "MCU", "type": "PART", "unit": "EA",
  "lead_time_days": 56, "moq": 500, "safety_stock": 300, "high_value": true,
  "unit_price": 3500, "supplier_id": "SUP-SEMION" }
// type: FG(완제품) | ASSY(반제품 — 재고·발주 없음, BOM 단계로만 있음) | PART(구매 부품)
// FG·ASSY는 lead_time_days·moq·unit_price·supplier_id가 null

// get_inventory  GET /internal/inventory?type=PART      (type·item_id 둘 다 선택)
{ "as_of": "2026-11-02",
  "items": [{ "item_id": "MCU", "name": "MCU", "type": "PART", "on_hand": 2700, "safety_stock": 300 }] }

// list_plans  GET /internal/plans?from=2026-11-20&to=2026-12-31   (양끝 포함, 둘 다 선택)
{ "plans": [{ "plan_id": "PP-05", "product_id": "FG-HUB", "plan_date": "2026-12-14", "qty": 400, "status": "PLANNED" }] }

// list_receipts  GET /internal/purchase-orders?status=OPEN&item_id=MCU&from=&to=   (status 기본 OPEN, from·to는 입고일)
{ "purchase_orders": [{ "po_id": "PO-2609-001", "item_id": "MCU", "supplier_id": "SUP-SEMION", "qty": 500,
    "unit_price": 3500, "amount": 1750000, "order_date": "2026-09-01", "due_date": "2026-12-12",
    "status": "OPEN", "parent_po_id": null }] }

// request_due_date_change  POST /internal/purchase-orders/PO-2609-001/due-date
//   body { "new_due_date": "2026-11-25", "approval_id": "..." }
{ "po_id": "PO-2609-001", "before": { "due_date": "2026-12-12" }, "after": { "due_date": "2026-11-25" }, "status": "OPEN" }

// split_po  POST /internal/purchase-orders/PO-2609-001/split
//   body { "split_qty": 100, "new_due_date": "2026-11-25", "approval_id": "..." }
{ "original": { "po_id": "PO-2609-001", "qty": 400, "due_date": "2026-12-12", "status": "OPEN" },
  "new":      { "po_id": "PO-2609-010", "qty": 100, "due_date": "2026-11-25", "status": "OPEN", "parent_po_id": "PO-2609-001" } }
```

- 요청 body: `analyze_impact`는 `{ "whatif": [...] }`, `compare_options`는 `{ "issues": [...] }` (`issues`는 `analyze_impact`의 `shortages` 항목을 그대로 넣는다).
- 변경 2개는 고정 응답이어도 **검사는 진짜로 한다** (승인 ID 유무 → OPEN → 수량 → 리드타임 → 날짜 같음). 오류 경로를 지금 시험할 수 있다. 시험용으로 이미 입고된 `PO-2608-014`가 있다. 저장은 아직 안 한다.
- 금액 필드(`unit_price`·`amount`·`added_cost`)는 지금 가리지 않는다. 마스킹(1.2)이 붙으면 **키는 남기고 값을 `null`로** 바꾼다 — 키가 사라지면 모양이 역할마다 달라져서 도구 결과를 읽는 쪽이 분기해야 한다.

#### 2차 (조회 폭을 넓히는 것) — 5개

| 도구 | Method · Path | 승인 |
|---|---|:-:|
| `explode_bom` | GET `/internal/bom/{product_id}/explode?on_date=` | |
| `where_used` | GET `/internal/bom/where-used/{item_id}` | |
| `get_supplier_metrics` | GET `/internal/suppliers/metrics?item_id=` | |
| `change_po_qty` | POST `/internal/purchase-orders/{id}/qty` | ✅ |
| `cancel_po` | POST `/internal/purchase-orders/{id}/cancel` | ✅ |

### 2.2 `analyze_impact` 요청과 응답

✅ **실제 계산으로 동작한다** (`ImpactAnalysis.java`, 골든셋 테스트 통과). 아래는 SC1의 실제 응답이다.

**요청** — `{ "whatif": [ {op, ...인자}, ... ] }`. 비우면 지금 상태 그대로 판정한다 (seed 기준 0건). 여러 개를 넣으면 순서대로 적용한다.

| `op` | 인자 | 쓰는 곳 |
|---|---|---|
| `reschedule_plan` | `plan_id`, `new_date` | SC1·SC3 일정 변경 |
| `change_plan_qty` | `plan_id`, `new_qty` | SC2 수량 변경 |
| `request_due_date_change` | `po_id`, `new_due_date` | SC4 입고 지연, 대안 검증 |
| `split_po` | `po_id`, `split_qty`, `new_due_date` | 대안 검증 |
| `change_po_qty` | `po_id`, `new_qty` | 대안 검증 |
| `cancel_po` | `po_id` | 대안 검증 |

- 발주 쪽 `op` 이름과 인자는 **변경 도구와 같다.** verify는 `compare_options`의 `tool`·`args`를 그대로 `{"op": tool, **args}`로 넣으면 된다 (2.4). `approval_id`는 넣어도 무시한다.
- 가정이라서 리드타임·승인은 보지 않는다. "실행할 수 없는 안이어도 어떻게 되는지"는 답한다. 날짜는 기준일(2026-11-02) 이후만 받는다.

**응답**

```jsonc
{
  "policy_version": "v0.1",
  "shortages": [                       // INV-001: P(t) < 0
    { "rule_id": "INV-001", "item_id": "MCU", "plan_id": "PP-05",
      "plan_date": "2026-11-25", "projected_stock": -100, "threshold": 0, "qty": 100,
      "orderable": false },            // LT-001: 오늘 새로 발주해서 plan_date까지 받을 수 있나
    { "rule_id": "INV-001", "item_id": "MCU", "plan_id": "PP-04",
      "plan_date": "2026-11-30", "projected_stock": -100, "threshold": 0, "qty": 100,
      "orderable": false }
  ],
  "warnings": [                        // INV-002: 0 ≤ P(t) < 안전재고 (SC2의 예)
    { "rule_id": "INV-002", "item_id": "MCU", "plan_id": "PP-03",
      "plan_date": "2026-11-23", "projected_stock": 0, "threshold": 300 }
  ],
  "excesses": [                        // INV-003: 고가 부품만, 부품마다 가장 큰 날 1건 (SC3의 예)
    { "rule_id": "INV-003", "item_id": "CASE-DOOR-V1", "date": "2026-11-15",
      "projected_stock": 800, "threshold": 0, "qty": 800,
      "window_days": 30,                // N = max(리드타임, 30)
      "amount": 640000 }                // 원. 과잉량 × 단가 (마스킹 대상)
  ]
}
```

- 결품·경고는 생산계획 단위로 나온다(`plan_id`). 같은 부족분이 다음 생산까지 이어지면 그 날짜에 다시 나온다 — 합산하지 않는다 (아키텍처 1.2).
- 과잉의 `threshold`는 "안전재고 + 앞으로 N일 소요량"이다. `projected_stock`이 이 선을 `qty`만큼 넘었다는 뜻이다.
- **`evidence_uri`는 뺐다** (10/20 오후). backend는 어느 문서가 근거인지 모른다 — 근거 찾기는 rag-mcp `search_docs`의 일이다. 고정 응답 시절 agent 흐름을 보여 주려고 넣었던 값이다 ([decisions.md 6.12](decisions.md)).

- 경고 한 건마다 `rule_id`·`projected_stock`·`threshold`를 붙인다. 이게 있어야 AI가 "왜 결품인지"를 지어내지 않고 규칙 ID로 설명한다.
- **`orderable`** (10/20 오후 추가, 결품에만): `true`면 "지금 발주하면 제때 도착"이라 위험이 아니라 **정상 안내**다 (기획안 1.3 "추가 발주 필요"). `false`면 이미 낸 발주를 당기거나 나눠야 한다. 이 칸이 없으면 모델이 리드타임을 보고 직접 날짜 계산을 하게 된다 — 계산은 backend가 한다는 원칙(아키텍처 1.1)에 어긋난다.
- `policy_version`은 기준을 바꿔도 과거 결과를 해석할 수 있게 한다. Harness 평가셋 전후 비교에 그대로 쓰인다.
- 판정 수식과 경계 조건은 [아키텍처 1.2](BOM_Agent_아키텍처.md).

### 2.3 `compare_options` 응답

```jsonc
{
  "policy_version": "v0.1",
  "options": [{
    "option_id": "OPT-1",
    "type": "SPLIT_PO",               // SPLIT_PO | DUE_DATE_CHANGE | REALLOCATE
    "summary": "PO-2609-001(MCU 500개, 12/12)을 400/100으로 나눠 100개를 11/25에 받는다",
    "tool": "split_po",                // 승인 카드와 실행에 그대로 쓴다
    "args": { "po_id": "PO-2609-001", "split_qty": 100, "new_due_date": "2026-11-25" },
    "added_cost": 390000,              // 원. 긴급 비용(17일 × 20,000) + 분할 배송 수수료 50,000
    "risk": "LOW",                     // LOW | MID | HIGH. 협력사 on_time_rate 기반
    "feasible": true,                  // LT-001 통과 여부
    "side_effects": [],                // 다른 제품에 생기는 영향
    "evidence": ["doc://SUPPLY-2026-003/5.1"]
  }],
  "recommended_option_id": "OPT-1"
}
```

- `tool`·`args`를 그대로 담는 이유: 모델이 고른 대안을 **그대로 승인 카드로 올리고 그대로 실행**할 수 있다. 모델이 인자를 다시 만들지 않으니 숫자가 틀어질 여지가 없다.
- `added_cost`·`risk`는 B의 계산 서비스가 `supplier_metric`을 읽어 채운다 ([decisions.md 2절](decisions.md)).

### 2.4 `verify` 통과 기준 (C가 구현)

고른 대안을 적용해 `analyze_impact`를 **다시** 부른 뒤:

| 조건 | 판정 |
|---|---|
| 대상 결품이 해소됐다 **그리고** 다른 제품에 새 결품이 없다 | 통과 → 승인 요청 |
| 둘 중 하나라도 아니다 | 실패 → 다른 대안으로 **최대 2회** 재시도 |
| 2회 모두 실패 | 남은 대안과 사유를 정리해 `DONE` |

### 2.5 오류 응답 형식

backend는 업무 오류를 **4xx + 아래 형식**으로 준다. business-mcp는 셋을 한 문장으로 합쳐 `ToolError`로 던진다.

```json
{ "error": "BELOW_MOQ", "message": "최소 발주 수량 미만입니다.",
  "retry_hint": "최소 수량은 300개입니다. 300개 이상으로 다시 요청하세요." }
```

→ `ToolError("[BELOW_MOQ] 최소 발주 수량 미만입니다. 최소 수량은 300개입니다. ...")`

**`retry_hint`는 비워 두지 않는다.** 모델이 이걸 읽고 인자를 고쳐 스스로 다시 부른다. 여기가 평가표 MCP 20점의 "오류·예외 처리"다.

HTTP 상태는 `NOT_FOUND` 404 · `PO_NOT_OPEN` 409 · `APPROVAL_REQUIRED` 403 · 나머지 400. business-mcp는 상태 코드로 분기하지 말고 `error` 코드를 읽으면 된다. 날짜 형식이 틀린 `new_due_date`도 `LEADTIME_INFEASIBLE`로 돌려준다 (retry_hint에 형식과 가장 빠른 가능일).

| 코드 | 경우 | retry_hint |
|---|---|---|
| `NOT_FOUND` | 없는 품목·발주 | 비슷한 ID 목록 |
| `BELOW_MOQ` | 최소 발주 수량 미만 | 최소 수량 |
| `LEADTIME_INFEASIBLE` | 리드타임상 불가능한 입고일. 이미 낸 발주는 **발주일 + 리드타임**부터 가능 ([decisions.md 7.2](decisions.md)) | 가장 빠른 가능일 |
| `PO_NOT_OPEN` | 이미 입고·취소된 발주 | 현재 상태 |
| `INVALID_SPLIT_QTY` | 분할 수량이 범위 밖 | 가능한 범위 |
| `SPLIT_NOT_USEFUL` | 분할 후 입고일이 원래와 같음 | 나눌 이유가 없음 |
| `BOM_CYCLE` | 순환 BOM | 순환 경로 |
| `APPROVAL_REQUIRED` | 승인 없이 변경 시도 | 승인 절차 안내 |
| `UPSTREAM_TIMEOUT` | 백엔드 응답 지연 | 잠시 후 재시도 |
| `INVALID_ARGUMENT` | 인자가 없거나 형식이 틀림 (날짜 형식, 모르는 `op`, 0 이하 수량) — **10/20 오후 추가** | 맞는 형식이나 가능한 값 목록 |

> **판정과 오류를 섞지 않는다.** 결품·경고·과잉은 정상 응답의 **결과**다. 오류는 **호출 자체가 성립하지 않을 때**만 쓴다.

### 2.6 작업 실행 요청

```jsonc
// POST /agent/tasks
{ "taskId": "...", "threadId": "...", "userId": "u-01", "role": "BUYER",
  "question": "허브를 11/25로 당기면?",
  "whatif": null }
```

**`whatif`를 누가 채우나** — 문서가 두 가지로 읽혀서 여기서 못 박는다.

| 경우 | `whatif` | 이유 |
|---|---|---|
| 사람이 질문했다 | `null`. **agent의 `classify_intent`가 질문에서 뽑는다** | 자연어 파싱은 LLM의 일이다. backend는 LLM을 부르지 않는다 (교재 금지 사항) |
| 시스템이 자동 생성했다 (SC4 입고 지연) | **backend가 채운다** | 무엇이 바뀌었는지 backend가 이미 알고 있다. 모델이 추측할 이유가 없다 |

### 2.7 승인 재개

```jsonc
// POST /agent/tasks/{id}/resume
{ "threadId": "...", "approvalId": "...", "decision": "APPROVED", "reason": null }
```

- agent는 같은 `threadId`로 checkpointer에서 상태를 꺼내 이어간다.
- 변경 도구를 부를 때 `approval_id`를 인자에 넣는다. backend는 그 승인이 `APPROVED`이고 **도구·인자가 승인 카드와 같을 때만** 실행한다.

### 2.8 평가셋 파일 `eval/golden.json` (10/20 오후 추가)

**SC1~SC4 정답은 이 파일 하나에만 둔다.** backend 골든셋 테스트와 C 파트 Harness 평가가 같은 파일을 읽는다. 기획안 PDF의 숫자를 정답으로 쓰지 않는다.

| 칸 | 주인 |
|---|---|
| `question` · `role` · `expected_tools` | C |
| `whatif` · `expected` | B (`expected`는 계산으로만 채운다. 손계산 금지) |

- 형식·문항 추가 절차는 [`eval/README.md`](../eval/README.md).
- `whatif`는 C의 `classify_intent`가 질문에서 뽑아야 할 정답이기도 하다.
- 칸 이름을 바꾸거나 지우려면 상대 파트와 합의한다. 칸을 **더하는 것**은 각자 해도 된다 (상대 쪽 코드는 모르는 칸을 무시한다).

---

## 3. A ↔ B 계약 (frontend ↔ backend)

### 3.1 화면 API

| Method | Path | 응답 | 권한 |
|---|---|---|---|
| POST | `/api/auth/login` | JWT `{sub, role, exp}` | 모두 |
| POST | `/api/ai/tasks` | `202 {taskId}` | 로그인 |
| GET | `/api/ai/tasks/{id}` | `{status, answer, result, sources}` | 요청자 |
| GET | `/api/ai/tasks/{id}/stream` | `text/event-stream` | 요청자 |
| POST | `/api/ai/tasks/{id}/approval` | `202` | APPROVER |
| GET | `/api/ai/tasks?mine=true` | 목록 | 로그인 |
| GET | `/api/approvals?status=PENDING` | 승인 대기함 | APPROVER |
| GET | `/api/plans` · `/api/inventory` · `/api/purchase-orders` · `/api/items/{id}/bom` | 조회 | 로그인 (금액 마스킹) |
| PUT | `/api/plans/{id}` | 생산 일정 변경 | PLANNER |
| PUT | `/api/purchase-orders/{id}/due-date` | 협력사 지연 입력 | BUYER |

### 3.2 SSE 이벤트 6종

공통 필드: `type`, `taskId`, `seq`, `ts`

| type | 추가 필드 | 화면 |
|---|---|---|
| `TOKEN` | `text` | 답변 영역에 이어 붙임 |
| `TOOL_START` | `tool`, `label` | 진행 상태 |
| `TOOL_END` | `tool`, `ok`, `ms` | 진행 상태 |
| `APPROVAL_REQUIRED` | `approvalId`, `tool`, `args`, `summary`, `amount`, `evidence` | 승인 카드 |
| `DONE` | `status`, `answer`, `result`, `sources` | 스트림 닫고 목록 갱신 |
| `FAILED` | `errorCode`, `message`, `traceId` | 오류 표시 |

- 화면이 늦게 구독하거나 다시 연결하면 backend가 **처음부터 다시 보낸다.** A는 `seq`로 중복만 거르면 된다.
- `amount`는 받는 사람의 역할에 맞춰 backend가 가린 뒤 보낸다.

### 3.3 승인 흐름의 payload

```
agent ──▶ backend   { tool, args, summary, amount, evidence }
backend ──▶ 화면     위 + approvalId          (amount는 역할에 맞춰 마스킹)
화면 ──▶ backend     { approvalId, decision, reason }
backend ──▶ agent    { threadId, approvalId, decision, reason }
```

- `approvalId`는 **backend가 만든다.** agent는 이벤트를 보낼 때 그 값을 알 수 없다.
- 거절은 `reason` 필수.
- 승인 카드에는 **변경 전후를 함께** 보여 준다. 도구 이름과 인자만으로는 사람이 판단할 수 없다.

### 3.4 승인 정책

- **변경 도구는 금액과 무관하게 전부 승인을 받는다.** 임계값을 두지 않는다.
- 팀장은 자기가 요청한 건을 자기가 승인할 수 있다 (요청자 ≠ 승인자 규칙 없음).
- 승인 대기는 **만료되지 않는다.**
- 거절되면 agent가 사유를 반영해 다른 대안을 낸다. **2회**까지.

---

## 4. 파트 내부 — 혼자 정하면 되는 것

| 파트 | 알아서 정할 것 |
|---|---|
| A | 컴포넌트 구조, 상태 관리, 스타일, Vite 전환 시점 |
| B | 테이블 내부 구조, 인덱스, 모듈 분리, 계산 알고리즘, 트랜잭션 경계 |
| C | Graph 노드 구성, 프롬프트 내용, 청킹 크기, 임베딩 모델, MCP Client 재사용 방식 |

---

## 5. 확정된 기술 선택

| 항목 | 결정 | 한 줄 이유 |
|---|---|---|
| Vector DB | **pgvector** | PostgreSQL 하나로 끝난다. 컨테이너와 장애 지점이 줄어든다 |
| 인증 | **Spring Security + JWT** | 역할을 agent까지 넘기기 쉽다 |
| Agent 구현 | **A안 (직접 StateGraph)** | 설계도의 노드 이름과 코드가 1:1로 맞아 평가 증빙이 쉽다 |
| LLM | **주 모델 1 + 대체 모델 1** | Harness Recovery 증빙에 둘이 필요하다. 제공자를 다르게 두면 더 강한 증빙이 된다 |
| 대기열 | **RabbitMQ** (가산점, 후순위) | 큐·재시도·DLQ 설정이 단순하다. Kafka는 이 규모에 과하다 |
| Harness 한도 | **교재 권장값 그대로** — 8턴 · 120초 · 같은 도구 3회 · 429·503 2회 재시도 | 바꾸면 명세에 이유를 남겨야 한다. 바꿀 이유가 아직 없다 |
| 통합 발주 (A4) | **MVP 제외** | 도구·계산·승인이 한 벌 더 필요한데 평가표에 해당 항목이 없다. 장표에 향후 과제로 |
| 자동 분석 (B1) | **넣는다** | 작업 생성 한 번이면 되고, SC4 시연이 완성되며 대기열 가산점과 이어진다 |
| 근거 문서 (C3) | **3종** — 구매 규정 / 협력사 계약 조건 / ECN 3~5건 | 통합 발주를 뺐으므로 통합 발주 기준 문서도 뺀다 |
| 문서 주소 | `doc://{doc_id}/{clause_no}` · 조항 단위 청킹 | 뼈대에 이미 들어 있다 |

근거는 [decisions.md 6절](decisions.md)에 적었다.
