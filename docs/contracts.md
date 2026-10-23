# 파트 간 계약

> **2026-10-22 변경 — task 전체 실행**: 신규 v3 작업은 개별 승인이 검토 체크이며 즉시 실행되지 않는다. task 단위 재검증·원자 실행 및 수동 수정안 계약은 [task-batch-approval.md](task-batch-approval.md)를 따른다. 아래 기존 부품별 승인·실행 설명은 v2 작업에 해당한다. DB 구조 변경 없음.

> **2026-10-22 추가 — 부서 요청**: 생산→구매·구매→생산 요청, 최종 거절·읽음 알림, 동의한 생산계획의 실제 반영은 [coordination-requests.md](coordination-requests.md)를 따른다. 일반 채팅의 whatif는 계속 가정이며, 명시적으로 전송·동의한 연결 요청만 발주와 생산계획을 함께 실행한다. DB 구조 변경 없음.

> **이 문서의 역할**: 파트 경계를 넘는 약속만 모았다. 여기 적힌 것은 **바꾸려면 상대 파트와 합의**해야 하고, 여기 없는 것은 각 파트가 알아서 정하면 된다.
>
> **정한 사람**: 기술 선택은 교재와 평가표 기준으로 Claude가 정했다. 왜 그렇게 정했는지는 [decisions.md 6절](decisions.md). 바꾸고 싶으면 그 근거를 보고 뒤집으면 된다.
>
> **갱신**: 2026-10-22 — 아래 "최근 변경" 표의 44~50번 (Phase 2, 48~50번은 10/23). 2026-10-21 — 30~43번. 표는 **번호가 큰(최근) 것부터** 적고, 뒤 결정으로 바뀐 행에는 "→ n번" 표시를 붙였다.

## 최근 변경 — 각 파트가 확인할 것 (10/20~10/22, 모든 파트)

> 맨 위에 두는 이유: 계약이 바뀐 줄이 문서 곳곳에 흩어져 있어서, 무엇이 바뀌었고 **누가 무엇을 해야 하는지**를 한 번에 보게 하려는 것이다. 다음 변경이 생기면 이 표를 새로 쓴다.

| # | 바뀐 것 | 어디 | A 할 일 | C 할 일 |
|:-:|---|---|---|---|
| 50 | **생산계획 변경 효과** — 영향 분석 질문이 생산계획을 바꾸고 다른 가정(입고 지연 등)도 있으면 `DONE.result.plan_effect{resolved[], new[], remaining[]}`를 **덧붙인다** (결품 항목 모양, remaining은 `before_qty` 추가). 그 계획 변경만 뺀 가정과 (부품, 생산계획) 단위로 비교한 것. 생산계획을 바꾼 영향 분석 답변 끝에 "생산계획은 아직 변경되지 않았습니다 …" 문장. 기존 칸은 그대로 | 3.2, decisions 6.52 | 답변 문장으로 표시됨(화면 변경 없음). 필요하면 `plan_effect`로 표 | ✅ 반영함: `plan_effect`·`effect_lines`, `restates_previous`, `classify_follow_up.md`·`impact.md` |
| 49 | **공급 지연 장기화 비교 + 결품 근거** — `analyze_impact` 결품 항목에 `basis{on_hand, receipts[{date, qty}], uses[{plan_id, plan_date, qty}]}` 추가 (재고 + 그날까지 입고 − 그날까지 소요 = `projected_stock`). 질문이 "약 1주 늦어진다"처럼 기간만 말하면 agent가 **원래 입고 예정일 + 일수**로 날짜를 채운다(분류 칸 `delay_days`). "더 길어지면"을 물은 발주(`may_extend`)는 agent가 1·4·12주(7·28·84일)로 각각 계산해 `DONE.result.scenarios[{weeks, delay_days, due_dates, impact}]`를 **덧붙인다**. `impact`는 통보 기준 그대로이고, 장기화 비교에서는 해결안·승인 카드를 만들지 않는다. whatif 계약(op·인자)은 그대로 | 2.2, 3.2, decisions 6.50 | ✅ 반영함: `ServerResult.vue` 결품마다 근거 한 줄, 아래 "장기화 대비 비교" 표(새로 생긴 결품 표시) | ✅ 반영함: `WhatifOp.delay_days·may_extend`, `resolve_delays`·`scenario_ops`, `delay_scenarios`, `classify.md`·`impact.md` |
| 48 | **해결 요청이 분석만으로 끝나지 않게** — 이어 묻기는 바로 앞 질문이 규정 질문(QA)·되묻기(CLARIFY)면 **최대 3단계 거슬러 올라가** 가정을 찾는다. 해결 요청인데 풀 문제가 없으면 `DONE.answer` 첫 줄에 이유를 쓴다(가정이 있으면 "해결안을 만들지 않았습니다 · 이미 풀렸을 수 있습니다", 가정도 없으면 "어떤 변경에 대한 해결인지 알려 주세요"). "해결방안·해결책·해결안·어떻게 해결·대안 있어"도 해결 요청으로 본다. **API·이벤트 모양은 그대로** | decisions 6.48 | 없음 (답변 문구만 바뀜) | ✅ 반영함: `previous_turn`, `_adjust_answer`, `ADJUST_REQUEST`, `prompts/classify.md` 예시 |
| 47 | **여유 있는 결품 해결안 + 팀장 조건 재제안** — `compare_options` 결품 대안이 딱 맞춘 안(첫 결품일·부족량)에 더해 **안전재고까지 채우는 안**과 **3일 일찍 받는 안**을 함께 낸다. 대안마다 `buffer{days, extra_qty}`, `safety_ok`(풀려던 생산에 안전재고 미만이 안 남음). 정렬·추천은 결품 해결 → 부작용 없음 → **안전재고까지** → 위험 → 비용. 요청에 선택 칸 `constraint{po_id?, new_due_date?, split_qty?}`를 주면 결품 대안을 그 조건으로만 만들고 못 지키는 이유는 `constraint_notes[]`(리드타임상 가장 빠른 가능일 등). 재요청(REPLAN)의 `args`가 이 조건이다 | 2.3, decisions 6.47, `eval/golden.json` SC1·SC2·SC4 대안 정답 갱신 | 🔶 팀원: [AI에게 재요청]에 원하는 입고일·분할 수량 입력칸, 대안 없음의 `outcome.detail` 표시 (패치 전달) | ✅ 재제안에서 조건을 계산 서비스에 넘기고 조건대로 계산된 첫 안을 코드가 고름. 🔶 팀원: `task_batch.py` REPLAN이 `args`를 `constraint`로 넘김 (패치 전달) |
| 46 | **생산·구매 부서 요청** — 전송 동의한 생산 변경과 해결안을 연결. 팀장 전체 실행 시 발주+계획 원자 반영, 요청 전체 최종 거절 사유 알림. 구매 확정 지연에서 생산 조정 요청 및 연결 요청 지원 | decisions 6.46, [API·화면·검증](coordination-requests.md) | 요청함·읽음·담당 접수·생산 변경 검토 UI. 실제 JWT 권한과 기존 이벤트 저장 사용 | 인증된 coordination 컨텍스트, 고정 whatif, 종료 상태 동기화. DONE.result 기존 필드 유지, coordinationId·plan_changes 추가 |
| 45 | **Task 전체 검토·실행** — 개별 승인은 체크만 저장. 수정 필요는 AI 재요청/직접 수정안으로 분기. task 전체 재검증 후 선택한 변경을 원자 반영하고 실패 시 원복. 대화 이어하기로 생성한 작업에도 동일 적용 | decisions 6.45, [상세 계약·검증](task-batch-approval.md) | 검토 상태·전체 실행/취소 API와 공통 UI 사용 | 신규 v3 작업·영속 검토 상태·전체 실행 결과 사용. DB 구조 유지 |
| 44 | **이어 묻기 (Phase 2)** — `POST /api/ai/tasks`에 선택 칸 `parentTaskId`. backend는 **본인 작업인지** 확인(아니면 403 `ROLE_NOT_ALLOWED`, 작업 안 만듦)하고 새 작업·새 thread를 만들어 `/agent/tasks`에 `parentThreadId`를 넘긴다. agent는 앞 작업의 저장된 State에서 질문·가정을 읽어, 이어 묻는 말이면 **앞 가정 + 이번 변경**으로 계산하고 답변 앞에 이어받은 가정을 밝힌다. 새 주제는 이어받지 않는다. 결과 계약(`DONE.result`)은 그대로 | 2.6, 3.1, decisions 6.44 | ✅ 반영함: 같은 대화의 다음 질문에 앞 작업 ID를 실어 보냄, 입력칸 아래 "앞 질문에 이어서 묻습니다 · [새 대화]". 선택한 업무를 바꾸면 끊김 | ✅ 반영함: `classify_follow_up.md`, `Classification.follow_up`, `merge_ops`(합치기는 코드), 가짜 모델 규칙에도 같은 처리 |
| 43 | **작업 이력은 본인만** — 모든 역할에서 목록·상세·SSE를 작업 소유자로 제한. SYSTEM도 동일. 승인함 진행 정보는 별도 API | 3.1, decisions 6.43 | 승인함은 `/approval-progress` 사용. 타인 작업 결과 열기 제외 | AI 요청·승인 계약 유지 |
| 42 | **입고 정보 수동 수정** — OPEN 발주의 예정 수량·날짜를 함께 저장, 사유 필수, 변경 이력과 자동 영향 분석 | 3.1, decisions 6.42 | 발주·입고 상세의 수량·일정 폼 연결 | 기존 승인 실행 도구 유지. DB 구조 변경 없음 |
| 41 | **거절 후 재제안은 팀장에게 직접 전달** — 최초 제안만 구매 검토. 동일 작업·요청자·품목의 실제 거절을 검증한 재제안은 PENDING으로 등록. 전체 문제·이전 구매 메모 계승. 작업 카드에서 원 질문 숨김 | 3.3, decisions 6.40 | 작업 카드에는 전체 문제만 표시. 재제안 이전 카드 ID로 배치 | APPROVAL_REQUIRED에 previousApprovalId. 이미 등록된 재제안 초안은 정확한 approvalId로 복구. 승인 자동 실행 금지 |
| 40 | **작업 단위 구매 검토** — 전체 문제 설명 + 고정된 품목 문제 + 부품별 메모. 현재 작업의 초안 전체를 전송/철회. 팀장에게 DRAFT·WITHDRAWN 비노출 | 3.1·3.3, decisions 6.39, [구현·검증 기록](task-review-changes.md) | 공통 작업 검토 UI와 task API 사용. 카드별 전송 API 폐기 | 모든 카드 등록 뒤 TASK_REVIEW_READY. 기존 승인/거절·실행은 부품별 유지. DB 스키마 변경 없음 |
| 39 | **해결안 비교 화면 삭제** (팀장 포함 모두), AI 채팅창에 업무 검색·너비 조절 | decisions 6.38 | ✅ B가 반영함. 해결안은 채팅창 카드와 승인 대기함에서만 | 없음 (`compare_options`·`/api/analysis/options`는 그대로) |
| 38 | **Harness 관측·예산·모델 복구** (harness 브랜치, MaJaeHyun00) — `TRACE` 이벤트(업무 상태는 안 바뀜), 작업 목록 `trace`·상세 `traces[]`, 도구 결과·입력 토큰 예산, 모델 재시도·대체(`FALLBACK_MODEL`), LangSmith 추적(기본 꺼짐) | 아래 "Harness 관측 추가 계약", decisions 6.37, [harness-validation.md](harness-validation.md) | 작업 이력에 trace 표시 (반영됨) | 반영됨. `.env`에 `AGENT_MODEL_MODE`·`LANGSMITH_*`·예산 변수 추가 (`.env.example`) |
| 37 | **구매 담당자 검토 단계** — 구매 요청 카드는 `DRAFT`(초안)로 시작. 요청자가 문제 정의(AI가 채움)·메모를 달아 `submit`하면 팀장에게, `withdraw`하면 보내지 않음. 반려 뒤 재제안도 다시 초안으로. `APPROVAL_REQUIRED`에 `problem` 추가, resume `decision`에 `WITHDRAWN` 추가 (→ 검토 단위는 40번, 재제안 경로는 41번에서 바뀜) | 2.7·3.1·3.3, decisions 6.33 | ✅ B가 반영함: **AI 채팅창 카드**와 승인 대기함 양쪽에서 초안에 문제 정의·메모를 달아 보내기/보내지 않기(6.34), 팀장 화면의 요청자 검토 표시, 구매 할 일 "승인 요청 보내기 (초안)" | ✅ B가 반영함: `problem_text`, `approval_gate`의 WITHDRAWN 처리, `approval_summary.WITHDRAWN`. 통합 테스트는 초안을 먼저 보내고 결정, 새 시나리오 `draft` |
| 36 | **카드가 남아 있으면 작업 상태는 `WAITING_APPROVAL`** — 여러 장 중 하나를 결정해도 "승인 대기" 유지, 마지막 결정 뒤에만 `RUNNING` | 3.1, decisions 6.31 | 없음 (작업 이력·필터가 그대로 맞게 나온다) | 없음 |
| 35 | **거절 뒤 재검토 결과 안내** — 거절한 제안에 `outcome`(재제안 생성 / 대안 없음 / 검토 실패 / 재제안 한도 + 안내 + 다음 행동). `GET /api/ai/tasks/{id}`에 `progress`(제안별 최신 진행) 추가. 부분 해결안(줄이기만)은 내지 않는다 | 3.1, decisions 6.30 | ✅ B가 반영함 (사용자 지시): 승인 대기함의 거절 카드 바로 아래에 결과 표시 (팀장·요청자 모두) | ✅ B가 반영함 (사용자 지시): `graph.py`의 `skip_target`·`save_proposal`·`replan_prepare`가 결과를 붙이고 `PROPOSAL_PROGRESS`·`DONE.result.proposals`에 실린다. `blocked_targets`는 그대로. 확인 부탁 |
| 34 | 해결안을 모두 준비한 뒤 개별 카드 등록, 독립 승인·실행. AI의 영구 접수함과 실행 기록 추가 (→ 구매 검토는 37·40번에서 추가됨) | 2.7, 3.2, decisions 6.29, [검증 기록](ai-rag-integration-changes.md) | 작업별 승인 그룹·개별 접수·3초 갱신·진행 이벤트·기존 작업 다시 열기 구현 | 체크포인트와 카드 연결 보존, 최신 상태 재검증, 모호한 응답은 자동 재실행 금지 |
| 33 | **승인 대기함: 팀장 외에는 내가 요청한 카드만** — `GET /api/approvals`가 요청자 기준으로 거르고, 화면이 모든 역할에서 대기함을 불러온다. 승인·거절은 그대로 팀장만 | 3.1, decisions 6.28 | ✅ B가 반영함 (사용자 지시) | 없음 |
| 32 | **답변 가독성** — ① `analyze_impact` 항목에 `item_name`·`product_name` 추가 ② 영향 분석 답변의 결품·주의·과잉 목록과 연쇄 결품 줄은 **코드가 계산 결과에서 그대로** 쓰고, 모델은 결론 1~2문장만 ③ 프롬프트에 평문 규칙 ④ 화면이 답변을 결론·제목·목록으로, 근거를 문서 카드로 그림 | 2.2·3.2, decisions 6.27 | ✅ B가 반영함 (사용자 지시). QA 결과(`impact: null`)에 빈 영향 결과를 안 그리고, 실행한 해결안은 `post_execution_impact`를 보여 준다 (C 변경 기록 2·9번) | 확인 필요: `graph.py`의 `lead_text`·`chain_note`·`describe_impact`, `prompts/impact.md`·`qa.md`를 B가 고쳤다. 남은 것: 검색 근거의 관련성(SC2 질문에 도어센서 문서가 붙음) |
| 31 | **해결안 비교 화면은 구매팀장(APPROVER)만** — 메뉴·주소·링크·할 일 카드 모두. 구매 담당자는 AI 채팅으로 승인을 요청한다 (팀 토의) (→ 39번에서 화면 자체를 삭제) | decisions 6.25 | ✅ B가 반영함 (사용자 지시). 목데이터 모드의 검수·우선순위 단계는 생산관리·구매가 더 못 쓴다 — 확인 필요 | 없음. 구매 담당자의 해결 요청은 채팅으로만 들어온다 |
| 30 | AI 결품·과잉 순차 승인 및 실제 RAG 연결. `DONE.result` 기본 3칸 유지, ADJUST 실행 내역·실행 후 분석 필드 추가 | 3.2, [변경 기록](ai-rag-integration-changes.md) | 감량·취소 승인 카드의 `tool`별 표시, 순차 승인 대기함 갱신 확인 필요 | 구현·회귀 검사. 변경 내역은 A·B 공유 필요 |
| 29 | **AI 작업이 RabbitMQ 대기열을 거친다** (`ai.tasks`, 실패하면 30초·2분 뒤 다시, 3번째 실패는 DLQ + `FAILED`). 접수·승인 후 재개 둘 다. **꺼내 보내는 것도 backend**라 agent 쪽 HTTP 경로(`/agent/tasks`, `/resume`)는 그대로 | decisions 6.24 | 없음. 재시도 중에는 작업이 `QUEUED`로 최대 2분 30초 머문다. 새 오류 코드 `QUEUE_UNAVAILABLE`(대기열에도 못 넣음) | 없음. 같은 작업을 두 번 받지 않게 backend가 막는다 (이미 시작한 작업은 다시 안 보냄) |
| 28 | 화면용 분석 API `POST /api/analysis/impact`·`POST /api/analysis/options` `{whatif}` — AI 도구와 같은 계산, 로그인 역할대로 금액 가림 (→ 해결안 비교 화면은 39번에서 삭제) | 3.1 | ✅ 반영함: 업무 현황 위험, 해결안 비교, 생산계획 영향 미리보기 | 없음 |
| 27 | **RAG 근거 문서 6개** (`data/docs/`, `data/documents.json`) — 시스템 ID·결정에 맞춘 v1.2. 조항 단위 `doc://{doc_id}/{clause_no}` | 2.2 근거 | – | ✅ indexer·`search_docs`·pgvector·실제 원문 조회 구현, 하드코딩 삭제. 환경마다 최초 색인 필요 (`data/README.md`) |
| 26 | `compare_options` 대안에 **`side_effects` 명세 형식**(NEW·WORSENED, 전후 결품량, `product_id`)과 **`remaining_issues`** 추가 | 2.3 | – | verify가 `remaining_issues`로 "왜 안 풀렸는지"를 설명할 수 있다. 기존 `resolves`는 그대로 |
| 25 | 화면 API: `GET /api/items/{id}/bom`, `GET /api/suppliers/metrics`, `PUT /api/suppliers/{s}/items/{i}/metrics` (평가지표 입력, BUYER) | 3.1 | 평가지표 표·입력 연결됨 (반영됨) | 없음 |
| 24 | **협력사 지연 입력 → 자동 분석 작업** (`trigger = SYSTEM`, `whatif`를 backend가 채움, PLANNER 역할로 돌아 금액 없음). 응답에 `analysis_task_id` | 2.6·3.1 | 지연 입력 뒤 `analysis_task_id`로 결과 보여 주기, 생산관리 작업 목록에도 나옴 | 없음 (그대로 동작 확인) |
| 23 | `compare_options`가 **과잉 대안**도 낸다 (`CHANGE_QTY`·`CANCEL_PO`·입고 미루기), 대안에 `target_rule`(INV-001/INV-003)·`saved_amount` 추가 | 2.3 | 감량·취소 승인 카드 표시 확인 (30번) | ✅ `INV-003`도 개별 검증→승인→실행 흐름 연결 |
| 22 | **2차 도구 5개 동작** (`explode_bom`·`where_used`·`get_supplier_metrics`·`change_po_qty`·`cancel_po`) | 2.1 | – | ✅ business-mcp에 등록함. 🔴 **그래프가 언제 쓸지는 C가 정한다** (지금 그래프는 결품 대안만 실행) |
| 21 | **`/internal/**`에 서비스 토큰 필수** (`X-Service-Token`). agent 토큰은 이벤트 경로(`/internal/tasks/**`)만, business-mcp 토큰은 업무 경로만 열린다 | 1.1 | – | ✅ 반영함: business-mcp는 넘겨받은 토큰을 버리고 자기 토큰을, agent는 이벤트에 토큰을 싣는다 (`SERVICE_TOKEN` 환경변수) |
| 20 | **`DONE.result`는 `{intent, whatif, impact}`** — 결품은 `result.impact` 안 | 3.2 | 화면이 `result.impact`를 읽게 고침 (반영됨) | 지금 모양 그대로 (바꾸면 알려 주기) |
| 19 | **작업·이벤트가 DB에 남는다** (재시작해도 승인 대기 작업이 이어짐). `GET /api/ai/tasks/{id}`, `GET /api/ai/tasks?mine=true` 생김 (→ 작업 목록·조회 권한은 43번에서 "본인만"으로 바뀜) | 3.1 | 작업 이력 화면을 실제 목록으로 (반영됨) | 없음 |
| 18 | 재고 응답에 `supplier_id`·`supplier_name`·`lead_time_days`·`used_in`(들어가는 완제품 이름), 발주 응답에 `supplier_name` — **칸 추가만** | 2.1 | 자재 화면의 협력사·리드타임·공용 제품 표시에 쓴다 (반영됨) | 없음 |
| 17 | 오류 코드 추가: `ROLE_NOT_ALLOWED`(403) · `APPROVAL_NOT_PENDING`(409) · `PLAN_NOT_OPEN`(409) | 2.5 | 403 문구가 이미 있다 | `ROLE_NOT_ALLOWED`를 받으면 "구매 담당자에게 요청" 안내로 끝내기 |
| 16 | `compare_options`는 `whatif`가 있어야 대안이 나온다 | 2.3 | – | 🔴 **`agent-draft`의 business-mcp `compare_options`에 `whatif` 인자 추가** — 없으면 대안 0개 |
| 15 | compose가 프론트를 **목데이터 모드**로 빌드한다 (프론트 Dockerfile 기본값) | – | 12·13·14번 뒤 compose에 `VITE_DATA_MODE=api` 빌드 인자를 넣기 (B와 같이) | – |
| 14 | 조회 응답은 `{plans: [...]}`처럼 감싸져 있고 snake_case | 2.1 | 🔴 **`services/api.ts`에서 화면 타입으로 변환** (`plan_id`→`id`, `on_hand`→`stock` 등) | – |
| 13 | **로그인 요청 칸은 `user_id`** (프론트 잠정안은 `username`) | 3.1 | 🔴 **`services/api.ts`의 `username` → `user_id`** | – |
| 12 | 화면 변경 API 2개 — **프론트가 정한 요청 모양 그대로**: `PUT /api/plans/{id} {date, qty}`, `PUT /api/purchase-orders/{id}/due-date {due_date, reason}` | 3.1 | API 모드에서 바로 쓸 수 있다 | – |
| 11 | `APPROVAL_REQUIRED` 이벤트 응답이 `{approvalId}`, SSE 이벤트에 `before`·`after` 추가 | 3.2·3.3 | 승인 카드에 변경 전후 표시 | (참고) 같은 이벤트를 다시 보내도 카드는 하나 |
| 10 | **승인 흐름이 실제로 동작한다**: 카드 저장·대기함·승인/거절·agent 재개 요청, 변경 도구는 승인과 도구·인자가 같을 때만 저장 | 3.1·3.3 | 승인 대기함·승인 카드 화면을 실제 API로 | 🔴 **`agent-draft`를 main에 합치기** — 지금 main의 agent엔 `/resume`이 없어 승인 후 FAILED가 난다 |
| 9 | 새 발주(추가 발주·대체 협력사 구매)는 **도구로 열지 않는다** (B 제안, 이견 있으면 뒤집기) | decisions 6.16 | – | – |
| 8 | `analyze_impact` 응답에서 `evidence_uri` 제거, `compare_options`의 `evidence`는 빈 목록 | 2.2·2.3 | – | 근거는 `search_docs`로 |
| 7 | 오류 코드 `INVALID_ARGUMENT` 추가 | 2.5 | – | 없음 (그대로 전달) |
| 6 | 생산관리 담당자에게 추가 비용은 금액 대신 등급(`cost_level`) | 1.2 | 생산관리 화면에서 `cost_level` 표시 | – |
| 5 | 로그인 생김. `/api/**`는 JWT 필수, SSE는 `?access_token=` | 3.1 | 🔴 **로그인 화면, 모든 요청에 `Authorization` 헤더** (지금 화면에 임시 버튼을 넣어 뒀다) | – |
| 4 | **`X-Role`이 없으면 금액이 전부 가려진다** (생산관리 기준) | 1.1 | – | 🔴 **agent → business-mcp → backend로 `X-Role`·`X-User-Id` 헤더 전달**. backend가 `/agent/tasks`에 `userId`·`role`을 넣어 준다 |
| 3 | `compare_options` 요청에 `whatif` 추가, 응답에 `item_id`·`resolves`·`recommended`·`cost_level` | 2.3 | – | verify가 `{"op": tool, **args}`로 다시 계산 |
| 2 | `analyze_impact` whatif 연산 6개 | 2.2 | – | 🔴 **business-mcp 도구 설명에 6개 모두 적기**, `classify_intent` 출력 형식 |
| 1 | **평가셋 정답은 `eval/golden.json`** (기획안 PDF 아님) | 2.8 | 시연 질문 예시에 `question` 사용 가능 | 🔴 **Harness 평가가 이 파일을 읽게** |

> ✅ **10/20 밤 통합 확인** — AI 팀원의 `integration-draft`(agent + 프론트 API 연결) 위에 B 변경을 얹어 **질문 → 영향 분석 → 대안 비교 → 검증 → 승인 요청 → 팀장 승인 → agent 재개 → 분할 실행 → 답변**이 실제로 끝까지 도는 것을 확인했다 (가짜 모델 기준).
> 이 통합으로 **2·3·4·10·13·14·15·16번은 해결됐다.** 남은 것: 1번(Harness 평가가 `eval/golden.json`을 읽는지 — `agent/eval_golden.py`가 읽음, C 확인), 6번(생산관리 화면의 `cost_level` 표시, A).


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
- ✅ **backend 쪽 구현 완료 (10/20 저녁)**: `/agent/tasks` 요청에 `userId`·`role`을 넣어 보낸다 (JWT에서 꺼낸 값). `/internal/**`는 `X-Role`을 읽어 금액을 가린다.
- ✅ **`X-Service-Token` 검사 (10/20 밤)**: 서비스마다 토큰이 다르고 자기 경로만 열린다 — agent → `/internal/tasks/**`, business-mcp → 나머지. business-mcp는 agent에게서 넘겨받은 토큰을 버리고 자기 토큰으로 바꿔 보낸다. 값은 compose 환경변수(`AGENT_SERVICE_TOKEN`·`BUSINESS_MCP_SERVICE_TOKEN`, 기본값은 시연용).
- **`X-Role`이 없거나 모르는 값이면 `PLANNER` 기준으로 가린다** (금액 전부 `null`). 헤더를 빠뜨렸을 때 금액이 새는 것보다 안 보이는 쪽이 안전하다. 대소문자는 구분하지 않는다. → **C가 헤더를 안 넘기면 BUYER가 물어도 AI 답변에 금액이 안 나온다.**

### 1.2 역할별 금액 공개 범위 (규칙 SEC-001)

| 역할 | 볼 수 있는 것 |
|---|---|
| `PLANNER` 생산관리 | 금액 비공개. **추가 비용은 등급(`cost_level`)만** (기획안 2.4) |
| `BUYER` 구매 | 총액까지 |
| `APPROVER` 구매팀장 | 계약 단가까지 |

✅ **구현 완료 (10/20 저녁, `Masking.java`)** — 응답 JSON의 **칸 이름**으로 가린다. 어느 API든 같은 이름이면 같이 가려진다.

| 칸 | PLANNER | BUYER | APPROVER |
|---|:-:|:-:|:-:|
| `unit_price` (단가·계약 단가) | `null` | `null` | 보임 |
| `amount` · `added_cost` (총액·추가 비용) | `null` | 보임 | 보임 |
| `cost_level` (`LOW`·`MID`·`HIGH` = 30만 원 미만·100만 원 미만·그 이상) | 보임 | 보임 | 보임 |

- **키는 남기고 값만 `null`**이다. 역할마다 응답 모양이 달라지지 않는다.
- `saved_amount`(과잉 대안의 덜 사는 금액)도 `amount`와 같이 가린다 (10/20 밤).
- 1차 차단은 **backend masking**이 `/internal/**`와 `/api/**` 응답에서 한다. 규칙은 여기 한 곳에만 둔다. 역할은 `/api`는 JWT, `/internal`은 `X-Role`에서 읽는다.
- SSE `APPROVAL_REQUIRED`의 `amount`는 승인 API(7단계)를 만들 때 같은 규칙을 적용한다.
- 2차는 agent Harness가 도구 결과에 단가 필드가 남아 있으면 모델에 넘기기 전에 지운다.

### 1.3 DB 스키마와 계정

| 스키마 | 테이블 | 쓰는 파트 | 계정 |
|---|---|---|---|
| `public` | 업무·작업·승인 | B | `app` |
| `agent` | LangGraph checkpointer + `workflow_command`, `approval_binding`, `execution_attempt`, `workflow_outbox` | C | `agent` |
| `rag` | `doc_chunk` | C | `rag` |

- **B가 `init.sql`로 스키마 3개와 계정 3개를 만든다.** 각 계정은 자기 스키마에만 권한을 갖는다.
- `agent` 계정에는 `CREATE` 권한을 준다. checkpointer 테이블은 LangGraph가 직접 만든다.
- 계정을 나누는 이유: 아키텍처 11장 보안성 증빙에 "DB 계정 분리"가 들어 있다. init SQL 몇 줄이면 된다.
- **접속 정보** (✅ [`db/init.sql`](../db/init.sql)에 구현): 호스트 `postgres:5432`, DB `bom`, 계정·비밀번호 `app/app` · `agent/agent` · `rag/rag`. 포트를 밖으로 열지 않은 내부망 개발값이다.
  - 계정마다 `search_path`가 자기 스키마라 테이블 이름에 스키마를 안 붙여도 된다.
  - **pgvector 확장은 `rag` 스키마에 있다.** `rag` 계정은 `vector(1536)`처럼 그냥 쓰면 된다. 다른 계정에는 이 타입이 보이지 않는다.
  - DB를 쓰기 시작하는 서비스는 `depends_on: postgres: condition: service_healthy`를 붙인다 (healthcheck 있음). 다른 서비스도 모두 healthcheck가 있다 (decisions 6.41).
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
  "items": [{ "item_id": "MCU", "name": "MCU", "type": "PART", "on_hand": 2700, "safety_stock": 300,
              "supplier_id": "SUP-SEMION", "supplier_name": "세미온반도체", "lead_time_days": 56,
              "used_in": ["IoT 허브", "도어 센서", "스마트 벽스위치", "스마트 플러그"] }] }   // used_in 2개 이상 = 공용 부품

// list_plans  GET /internal/plans?from=2026-11-20&to=2026-12-31   (양끝 포함, 둘 다 선택)
{ "plans": [{ "plan_id": "PP-05", "product_id": "FG-HUB", "plan_date": "2026-12-14", "qty": 400, "status": "PLANNED" }] }

// list_receipts  GET /internal/purchase-orders?status=OPEN&item_id=MCU&from=&to=   (status 기본 OPEN, from·to는 입고일)
{ "purchase_orders": [{ "po_id": "PO-2609-001", "item_id": "MCU", "supplier_id": "SUP-SEMION", "supplier_name": "세미온반도체", "qty": 500,
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

- 요청 body: `analyze_impact`는 `{ "whatif": [...] }`, `compare_options`는 `{ "whatif": [...], "issues": [...] }` (2.3).
- 변경 2개는 고정 응답이어도 **검사는 진짜로 한다** (승인 ID 유무 → OPEN → 수량 → 리드타임 → 날짜 같음). 오류 경로를 지금 시험할 수 있다. 시험용으로 이미 입고된 `PO-2608-014`가 있다. 저장은 아직 안 한다.
- 금액 필드(`unit_price`·`amount`·`added_cost`)는 지금 가리지 않는다. 마스킹(1.2)이 붙으면 **키는 남기고 값을 `null`로** 바꾼다 — 키가 사라지면 모양이 역할마다 달라져서 도구 결과를 읽는 쪽이 분기해야 한다.

#### 2차 (조회 폭을 넓히는 것) — 5개

✅ **10/20 밤 구현** (backend + business-mcp 도구 등록). 응답 모양:

```jsonc
// explode_bom  GET /internal/bom/FG-HUB/explode?on_date=2026-11-02   (on_date 기본 기준일. 설계변경 유효일 때문에 날짜가 필요)
{ "product_id": "FG-HUB", "name": "IoT 허브", "on_date": "2026-11-02",
  "tree":  [{ "level": 1, "parent_id": "FG-HUB", "item_id": "ASSY-HUB-MB", "name": "허브 메인보드", "type": "ASSY", "qty_per": 1, "qty": 1 }, …],
  "parts": [{ "item_id": "MCU", "name": "MCU", "qty": 1 }, …] }      // 완제품 1대당 구매 부품 합계 (16종)

// where_used  GET /internal/bom/where-used/RELAY
{ "item_id": "RELAY", "on_date": "2026-11-02",
  "products": [{ "product_id": "FG-PLUG", "name": "스마트 플러그", "qty_per_unit": 1 }, { "product_id": "FG-SWITCH", …, "qty_per_unit": 2 }] }

// get_supplier_metrics  GET /internal/suppliers/metrics?item_id=RELAY   (item_id 빼면 전부. 주 협력사 먼저)
{ "metrics": [{ "supplier_id": "SUP-POWER", "supplier_name": "파워링크전자", "is_primary": true, "item_id": "RELAY", "item_name": "릴레이",
    "unit_price": 900, "lead_time_days": 28, "expedite_cost_per_day": 50, "split_fee": 30000, "delay_penalty": 150000,
    "on_time_rate": 78.0, "quality_score": 88, "evaluated_at": "2026-10-15", "evaluated_by": "buyer" }, …] }

// change_po_qty  POST /internal/purchase-orders/PO-2610-115/qty  { "new_qty": 500, "approval_id": "…" }
{ "po_id": "PO-2610-115", "before": { "qty": 800 }, "after": { "qty": 500 }, "status": "OPEN" }   // MOQ 미만이면 BELOW_MOQ

// cancel_po  POST /internal/purchase-orders/PO-2610-110/cancel  { "approval_id": "…" }
{ "po_id": "PO-2610-110", "before": { "status": "OPEN" }, "after": { "status": "CANCELLED" } }
```

- 변경 2개도 1차와 같은 규칙이다: 팀장 승인과 도구·인자가 같아야 실행, 변경·이력·승인 상태를 한 트랜잭션으로.
- 수량 변경은 MOQ를 본다 — 분할(나눠 받기, 3.4)과 달리 주문량 자체가 줄어든다.

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


- **이름 칸 (10/21 추가, decisions 6.27)**: `shortages`·`warnings`·`excesses`의 항목마다 `item_name`(부품 이름)이 붙고, `plan_id`가 있는 항목에는 `product_name`(그 계획의 제품 이름)도 붙는다. 칸 추가만이라 기존 소비자는 그대로 동작한다. AI는 영문 ID를 번역하지 말고 이 이름을 쓴다.
### 2.3 `compare_options` 요청과 응답

✅ **실제 계산으로 동작한다** (`OptionAnalysis.java`, 평가셋 SC1~SC4 대안 정답 테스트 통과). 아래는 SC1의 실제 응답 중 첫 대안이다.

**요청** — `{ "whatif": [...], "issues": [...], "constraint": {...}? }` (10/20 오후 변경: 전에는 `issues`만 있었다. 10/22 `constraint` 추가, 47번)
- `constraint` (선택): 팀장이 정한 `{po_id?, new_due_date?, split_qty?}`. 주면 결품 대안을 그 조건으로만 만들고, 못 지키는 발주는 이유를 `constraint_notes`에 담는다. 없으면 기본 후보(딱 맞춤 · 안전재고까지 · 3일 일찍).
- `whatif`: `analyze_impact`에 넣었던 것과 **같은 가정**. 결품은 가정 위에서만 생기므로 대안도 그 위에서 만들고 검증해야 한다. 변경이 이미 DB에 반영됐으면 비운다.
- `issues`: 풀고 싶은 결품. `analyze_impact`의 `shortages` 항목을 그대로 넣어도 된다 (`item_id`만 본다). 비우면 전부.

**응답**

```jsonc
{
  "policy_version": "v0.1",
  "options": [{
    "option_id": "OPT-1",
    "type": "SPLIT_PO",                // 결품: SPLIT_PO | DUE_DATE_CHANGE, 과잉: CHANGE_QTY | CANCEL_PO | DUE_DATE_CHANGE(미루기)
    "target_rule": "INV-001",          // 이 대안이 푸는 것: INV-001 결품 | INV-003 과잉
    "item_id": "MCU",                  // 이 대안이 푸는 부품
    "summary": "PO-2609-001(MCU 500개, 12/12 입고)에서 100개만 떼어 11/25에 먼저 받는다",
    "tool": "split_po",                // 승인 카드와 실행에 그대로 쓴다
    "args": { "po_id": "PO-2609-001", "split_qty": 100, "new_due_date": "2026-11-25" },
    "added_cost": 390000,              // 원. 당김 100개 × 17일 × 200원 + 분할 수수료 50,000 (마스킹 대상)
    "cost_level": "MID",               // 30만 미만 LOW · 100만 미만 MID · 그 이상 HIGH. 생산관리 담당자는 이것만 본다
    "risk": "MID",                     // LOW | MID | HIGH. 납기 준수율 + 당기는 일수
    "feasible": true,                  // LT-001 통과. 통과 못 한 대안은 아예 안 나오므로 항상 true
    "resolves": true,                  // 이 대안 하나로 그 부품의 결품(orderable=false)이 다 풀리나 — backend가 다시 계산해서 채움
    "side_effects": [],                // 이 대안 때문에 새로 생기거나(NEW) 더 커진(WORSENED) 결품 — 아래 형식
    "remaining_issues": [],            // 이 대안이 풀려던 결품 중 그대로 남은 것 — 아래 형식
    "recommended": true,               // 부품마다 하나만 true
    "evidence": []                     // backend는 비워 둔다. agent가 search_docs 결과로 채운다
  }],
  "recommended_option_id": "OPT-1"     // 첫 번째 추천안. 결품 부품이 여럿이면 options의 recommended=true를 모두 본다
}
```

**`side_effects`·`remaining_issues` 원소** (10/20 밤, [side_effects 계산 명세] 반영)

```jsonc
// side_effects[] — 대안을 적용한 뒤 (부품, 계획, 날짜)별 결품량이 적용 전보다 커진 곳
{ "type": "NEW_SHORTAGE",            // NEW_SHORTAGE(전엔 결품 아님) | WORSENED_SHORTAGE(전에도 결품, 더 모자람)
  "rule_id": "INV-001", "item_id": "COIN-CELL", "product_id": "FG-DOOR", "plan_id": "PP-02", "plan_date": "2027-01-04",
  "before_shortage_qty": 0, "after_shortage_qty": 600, "increase_qty": 600 }

// remaining_issues[] — 결품 대안: 풀려던 결품이 남음 / 과잉 대안: 그 부품의 과잉이 남음
{ "rule_id": "INV-001", "item_id": "MCU", "product_id": "FG-PLUG", "plan_id": "PP-04", "plan_date": "2026-11-30", "shortage_qty": 100 }
{ "rule_id": "INV-003", "item_id": "CASE-PLUG", "date": "2026-11-27", "excess_qty": 300 }
```

- 전후는 **같은 기준**으로 비교한다: 전 = DB + 원래 `whatif`, 후 = DB + 원래 `whatif` + 대안 `{op: tool, **args}`. DB는 바꾸지 않는다.
- 결품량은 날짜마다 따로 본다 (합산하지 않음). 결품량 = `max(0, −projected_stock)`.
- `side_effects`가 빈 목록이면 **계산으로 확인한 결과 부작용이 없다**는 뜻이다 (예전 고정값 `[]`가 아니다).
- `resolves`는 그대로 둔다: `remaining_issues`에 발주로 못 푸는 결품이 없으면 `true`. 추천은 `resolves = true`이고 `side_effects`가 빈 대안만 된다.

- `tool`·`args`를 그대로 담는 이유: 모델이 고른 대안을 **그대로 승인 카드로 올리고 그대로 실행**할 수 있다. 모델이 인자를 다시 만들지 않으니 숫자가 틀어질 여지가 없다.
- **과잉 대안** (10/20 밤): 과잉 부품마다, 과잉이 잡힌 날까지 들어오는 발주를 과잉량만큼 줄이거나(MOQ 미만이 되면 취소) 다음 사용일로 미룬다. 비용은 0, 대신 덜 사는 금액 `saved_amount`(원, 마스킹 대상). 줄이다 같은 부품이 모자라게 되면 `side_effects`에 나와 추천되지 않는다 (SC3 코인전지 취소가 그 예). 결품 대안이 항상 먼저 정렬된다.
- 결품 대안은 **`orderable=false` 결품만** 대상으로 만든다. `orderable=true`는 "지금 발주하면 제때 온다"는 정상 안내라서 기존 발주를 건드릴 이유가 없다. 그래서 SC3(새 케이스 −800, 발주 가능)은 대안이 0개다.
- **추천 순서**: 결품을 푸는 것 → 부작용 없는 것 → 위험 낮은 것 → 싼 것. 결품을 다 풀면서 부작용이 없는 대안이 없으면 `recommended_option_id`는 `null`이다. 근거는 [decisions.md 6.14](decisions.md).
- **결품 부품이 여럿이면 추천안도 여럿이다** (SC2: 릴레이·전원 모듈 각 1개). 둘 다 승인해야 결품이 다 풀린다. 승인 요청은 대안마다 따로 올린다 (승인 카드 1장 = 도구 호출 1번).
- `added_cost`·`risk`는 `supplier_metric`에서 나온다 ([decisions.md 2절](decisions.md)).

### 2.4 `verify` 통과 기준 (C가 구현)

고른 대안을 적용해 `analyze_impact`를 **다시** 부른 뒤 (whatif = 원래 가정 + `{"op": tool, **args}`):

> `compare_options`가 이미 같은 계산으로 `resolves`·`side_effects`를 채워 준다. verify는 그 값을 믿어도 되고, Harness 증빙을 위해 다시 불러 확인해도 된다. 결과는 같다.


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
| `APPROVAL_REQUIRED` | 승인 없이 변경 시도. 승인이 PENDING·REJECTED·EXECUTED이거나 **도구·인자가 승인 카드와 다를 때**도 | 승인 절차, 또는 승인된 호출 그대로의 인자 |
| `UPSTREAM_TIMEOUT` | 백엔드 응답 지연 | 잠시 후 재시도 |
| `SERVICE_TOKEN_REQUIRED` | `/internal`에 서비스 토큰이 없거나 틀림 (401) — **10/20 밤 추가**. 설정 문제라 모델이 고칠 수 없다 | 어느 서비스의 `SERVICE_TOKEN`을 확인할지 |
| `INVALID_ARGUMENT` | 인자가 없거나 형식이 틀림 (날짜 형식, 모르는 `op`, 0 이하 수량) — **10/20 오후 추가** | 맞는 형식이나 가능한 값 목록 |
| `ROLE_NOT_ALLOWED` | 이 역할로는 못 하는 일. agent에선 생산관리 담당자 작업의 승인 요청 (403) — **10/20 밤 추가** | 할 수 있는 역할 |
| `APPROVAL_NOT_PENDING` | 이미 처리된 승인을 다시 결정 (409, 화면) — **10/20 밤 추가** | 현재 상태 |
| `PLAN_NOT_OPEN` | 끝났거나 취소된 생산계획 변경 (409, 화면) — **10/20 밤 추가** | 현재 상태 |

> **판정과 오류를 섞지 않는다.** 결품·경고·과잉은 정상 응답의 **결과**다. 오류는 **호출 자체가 성립하지 않을 때**만 쓴다.

### 2.6 작업 실행 요청

```jsonc
// POST /agent/tasks
{ "taskId": "...", "threadId": "...", "userId": "u-01", "role": "BUYER",
  "question": "허브를 11/25로 당기면?",
  "whatif": null,
  "parentThreadId": null }   // 이어 묻는 질문이면 앞 작업의 thread (10/22 추가, 44번). 소유자 확인은 backend가 끝냈다
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
- 10/21부터 agent는 시작·결정을 `agent.workflow_command`에 저장한 뒤 `202`를 반환한다. 그래프가 다른 카드를 등록/실행 중이어도 접수한다. 시작은 task ID, 결정은 approval ID로 중복을 거르며 같은 ID의 다른 payload는 409다. `AGENT_DB_URL`이 없으면 503으로 거부한다.
- 동일 thread의 처리는 PostgreSQL 잠금으로 직렬화한다. 대기 중인 다른 카드의 사람 결정을 기다릴 필요는 없다. 준비된 대안에 대응하는 `approvalId`만 실행하며, 거절 재제안에는 새 proposal/revision과 새 승인이 필요하다.
- 변경 도구를 부를 때 `approval_id`를 인자에 넣는다. backend는 그 승인이 `APPROVED`이고 **도구·인자가 승인 카드와 같을 때만** 실행한다.

### 2.8 평가셋 파일 `eval/golden.json` (10/20 오후 추가)

**SC1~SC4 정답은 이 파일 하나에만 둔다.** backend 골든셋 테스트와 C 파트 Harness 평가가 같은 파일을 읽는다. 기획안 PDF의 숫자를 정답으로 쓰지 않는다.

| 칸 | 주인 |
|---|---|
| `question` · `role` · `expected_tools` | C |
| `whatif` · `expected` | B (`expected`는 계산으로만 채운다. 손계산 금지). `expected.options`·`recommended_option_id`는 `compare_options` 정답 |

- 형식·문항 추가 절차는 [`eval/README.md`](../eval/README.md).
- `whatif`는 C의 `classify_intent`가 질문에서 뽑아야 할 정답이기도 하다.
- 칸 이름을 바꾸거나 지우려면 상대 파트와 합의한다. 칸을 **더하는 것**은 각자 해도 된다 (상대 쪽 코드는 모르는 칸을 무시한다).

---

## 3. A ↔ B 계약 (frontend ↔ backend)

### 3.1 화면 API

| Method | Path | 응답 | 권한 |
|---|---|---|---|
| POST | `/api/auth/login` | ✅ `{token, user_id, name, role, expires_at}` | 모두 |
| POST | `/api/ai/tasks` | ✅ `202 {taskId}`. 요청 `{question, parentTaskId?}` — `parentTaskId`는 이어 묻기(44번), 본인 작업이 아니면 403 | 로그인 |
| GET | `/api/ai/tasks/{id}` | ✅ 작업 한 건 (아래) | 작업 소유자만. 다른 사람은 역할·trigger 무관하게 403 |
| GET | `/api/ai/tasks/{id}/stream` | ✅ `text/event-stream` | 요청자 (다른 사람이면 403) |
| GET | `/api/ai/tasks/{id}/approval-progress` | `{progress:[{approvalId, proposalStatus, outcome}]}`. 질문·답변·분석 결과·trace 없음. 조회 가능한 카드만 반환 | 승인 요청자 또는 전달된 카드가 있는 APPROVER. DRAFT·WITHDRAWN은 팀장 비노출 |
| POST | `/api/ai/tasks/{id}/approval` | ✅ `202 {approvalId, status}` — `PENDING` 카드만 (초안은 `APPROVAL_NOT_PENDING`) | APPROVER |
| POST | `/api/ai/tasks/{id}/review/submit` | 현재 초안 전체를 한 트랜잭션으로 `DRAFT → PENDING`. `taskProblem` 필수. 아래 작업 검토 계약 참조 | 요청자 본인(BUYER 이상) |
| POST | `/api/ai/tasks/{id}/review/withdraw` | 현재 초안 전체를 한 트랜잭션으로 `DRAFT → WITHDRAWN`. 부품별 재개 알림은 영속 상태를 기준으로 재시도 | 요청자 본인(BUYER 이상) |
| GET | `/api/ai/tasks?mine=true` | ✅ 본인 작업 목록, 최근 50개. `mine` 생략·false도 본인만. 본인이 시작한 SYSTEM 작업 포함 | 로그인 |
| GET | `/api/approvals?status=PENDING` · `/api/approvals/{id}` | ✅ 승인 카드 목록 · 카드 하나 (3.3). **APPROVER에게 DRAFT·WITHDRAWN은 제외, 다른 역할은 자기가 요청한 카드만** (남의 카드 하나를 열면 403 `ROLE_NOT_ALLOWED`, decisions 6.28) | 전부 (요청자 기준으로 거름) |
| GET | ✅ `/api/plans` · `/api/inventory` · `/api/purchase-orders` · `/api/items/{id}/bom` · `/api/suppliers/metrics` (응답 모양은 2.1 `/internal`과 같다) | 조회 | 로그인 (금액 마스킹) |
| POST | ✅ `/api/analysis/impact` · `/api/analysis/options` `{whatif, issues?}` | AI 도구 2.2·2.3과 같은 모양. `whatif`를 비우면 지금 DB 상태 그대로 (업무 현황 위험), 넣으면 미리보기 (생산계획 영향 미리보기) (DB는 안 바뀜) | 로그인 (금액 마스킹) |
| PUT | ✅ `/api/suppliers/{supplier_id}/items/{item_id}/metrics` `{on_time_rate?, quality_score?, unit_price?, lead_time_days?, expedite_cost_per_day?, split_fee?, delay_penalty?}` | 바뀐 지표 한 줄. 보낸 칸만 바꾸고 `evaluated_at`·`evaluated_by`를 남긴다 | BUYER·APPROVER (AI 도구로는 안 연다) |
| PUT | `/api/plans/{id}` `{date?, qty?, reason?}` | ✅ 바뀐 계획 한 건 (목록과 같은 모양) | PLANNER만 (팀장도 안 됨) |
| PUT | `/api/purchase-orders/{id}/due-date` `{due_date, reason}` | ✅ 바뀐 발주 한 건 (목록과 같은 모양) + `analysis_task_id`(자동 분석 작업). 사유 필수 | BUYER·APPROVER |
| PUT | `/api/purchase-orders/{id}` `{qty?, due_date?, reason}` | 변경된 발주 값 + `analysis_task_id`. OPEN만, 수량은 양의 정수, 날짜는 기준일·발주일 이전 불가. 사유 필수. 변경 없음은 400. `update_purchase` 전후 이력 저장 후 자동 분석 | BUYER·APPROVER |

✅ = 구현됨 (10/20). 3.1 화면 API는 전부 구현됐다.

**작업 한 건** (`GET /api/ai/tasks/{id}`, 목록의 한 줄) — 작업 API는 처음부터 camelCase(`taskId`)라 그대로 맞췄다:

```jsonc
{ "taskId": "…", "userId": "buyer", "role": "BUYER", "question": "…", "trigger": "USER",   // SYSTEM = 자동 분석
  "status": "DONE",        // QUEUED → RUNNING → WAITING_APPROVAL → RUNNING → DONE | FAILED. 대기 카드가 남아 있으면 결정해도 WAITING_APPROVAL (decisions 6.31)
  "answer": "…", "result": { … }, "sources": [ … ],   // DONE 이벤트 내용. result의 금액은 보는 사람 역할대로 가린다
  "createdAt": "…", "finishedAt": "…",
  // 한 건 조회에만 (10/21, decisions 6.30): 제안별 최신 PROPOSAL_PROGRESS. 작업이 안 끝나도 거절 카드의 재검토 결과를 보여 준다
  "progress": [ { "proposalId": "…", "approvalId": "APR-…", "proposalStatus": "REJECTED",
                  "outcome": { "code": "NO_ALTERNATIVE", "item_name": "도어센서 옛 케이스", "message": "…", "next_action": "…", "detail": null } } ] }
```
- `outcome.code`: `REPROPOSED`(재제안 생성) · `NO_ALTERNATIVE`(대안 없음) · `REVIEW_FAILED`(검토 실패, 대안이 없다는 뜻이 아님) · `LIMIT`(재제안 한도). 거절한 제안에만 붙는다. 같은 칸이 `DONE.result.proposals[].outcome`에도 있다.
 화면 변경 2개의 요청 모양은 프론트 브랜치가 먼저 정한 것을 따랐다.

**로그인과 토큰** (10/20 저녁 구현)
- 요청 `POST /api/auth/login {"user_id": "buyer", "password": "1234"}` → `{"token": "eyJ…", "user_id": "buyer", "name": "박구매", "role": "BUYER", "expires_at": "…"}`
- 실패는 `401 {"error": "INVALID_CREDENTIALS", …}`. 아이디가 틀렸는지 비밀번호가 틀렸는지 알려 주지 않는다.
- 이후 모든 `/api/**` 요청에 `Authorization: Bearer {token}`. 없거나 만료면 `401`.
- **SSE만 예외**: `GET /api/ai/tasks/{id}/stream?access_token={token}`. 브라우저 `EventSource`는 헤더를 못 붙인다. 다른 경로에서는 주소의 토큰을 받지 않는다.
- 토큰 유효 8시간. 갱신 토큰은 없다 — 만료되면 다시 로그인.
- 응답의 `role`·`name`은 화면 표시에만 쓴다. 권한 판단은 backend가 한다 (교재 5장).
- 시연 계정: `planner`(김계획) · `buyer`(박구매) · `approver`(정팀장), 비밀번호 `1234`.

### 3.2 SSE 이벤트 (실제로 보내는 9종 + 예약 1종)

공통 필드: `type`, `taskId`, `seq`, `ts`

| type | 추가 필드 | 화면 |
|---|---|---|
| `TOKEN` | `text` | **예약만 하고 보내지 않는다.** 답변은 완성된 뒤 `DONE.answer`로 한 번에 보낸다 — 목록·숫자를 코드가 계산 결과에서 붙이는 구조(decisions 6.27)라 토큰 단위로 흘려보낼 중간 글이 없다. 화면은 받을 준비만 되어 있다 |
| `TOOL_START` | `tool`, `label` | 진행 상태 |
| `TOOL_END` | `tool`, `ok`, `ms` | 진행 상태 |
| `APPROVAL_REQUIRED` | `approvalId`, `tool`, `args`, `summary`, `amount`, `evidence`, ✅ `before`, `after` | 승인 카드 |
| `PROPOSAL_PROGRESS` | `proposalId`, `approvalId`, `revision`, `replaces`, `itemId`, `summary`, `proposalStatus`, `message`, `eventId`, `version` | 요청자 화면의 카드별 승인 대기·실행 중·완료·거절·보류·확인 필요 |
| `WORKFLOW_NOTICE` | `message`, 선택 `approvalId`/`errorCode`, `eventId`, `version` | 종료로 간주하지 않는 복구/확인 안내 |
| `TASK_REVIEW_READY` | `reviewId`, `approvalIds` | 구매가 작업 단위로 검토할 카드가 모두 등록됨 (아래 "작업 단위 구매 검토 계약") |
| `TRACE` | `traceId`, `previousTraceId`, `executionKind`, `outcome`, `traceUrl`, 실행 통계 | 업무 상태는 안 바뀜. 작업 이력의 실행 기록 (아래 "Harness 관측 추가 계약") |
| `DONE` | `status`, `answer`, `result`, `sources` — `result` 모양은 아래 | 스트림 닫고 목록 갱신 |
| `FAILED` | `errorCode`, `message`, `traceId` | 오류 표시 |

- 화면이 늦게 구독하거나 다시 연결하면 backend가 **처음부터 다시 보낸다.** A는 `seq`로 중복만 거르면 된다.
- 영구 발행함 재전송은 `seq`가 달라도 같은 `eventId`일 수 있다. 프론트는 둘 다 중복 제거하고 proposal별 큰 `version`만 반영한다. 추가 이벤트는 기존 backend가 jsonb로 그대로 저장·중계하므로 backend 수정이 없다. 전체 작업 상태를 이 이벤트로 바꾸지는 않는다.
- `amount`는 받는 사람의 역할에 맞춰 backend가 가린 뒤 보낸다.

**`DONE.result` 모양** (10/20 밤 확정 — 그동안 칸만 있고 안의 모양을 정하지 않아 화면이 결품을 못 찾았다)

```jsonc
{ "intent": "IMPACT",          // agent가 분류한 의도 (IMPACT · ADJUST · QA …)
  "whatif": [ … ],             // 질문에서 뽑은 가정 변경 (contracts 2.2 연산)
  "impact": {                  // analyze_impact 결과 그대로 (2.2). 영향 분석을 안 한 질문이면 null
    "policy_version": "v0.1", "shortages": [ … ], "warnings": [ … ], "excesses": [ … ] } }
```

- 화면은 `result.impact`에서 결품·주의·과잉을 그린다. `impact`가 없으면 `result` 자체를 본다 (`ServerResult.vue`).
- 금액은 agent가 질문자 역할대로 한 번, `GET /api/ai/tasks/{id}`로 다시 볼 때 backend가 보는 사람 역할대로 한 번 더 가린다.

AI 변경(계약 표 30번): 위 `{intent, whatif, impact}`의 이름과 모양은 그대로 유지한다.
ADJUST의 `impact`는 **해결안 실행 전 최초 영향 분석**이고 `whatif`는 **사용자의 원래 가정**이다.
기존 ADJUST 상세 필드(`targets`, `selected_option`, `pick_reason`, `verify_log`, `decision`, `executed`) 외에
아래 필드를 추가했다. 화면이 실행 후 상태를 그리려면 `post_execution_impact`를 명시적으로 읽어야 한다.

| 추가 필드 | 의미 |
|---|---|
| `executions` | 성공한 실행 전부: `{tool, args, target_rule, item_id, approval_id, result}` 목록 |
| `post_execution_impact` | 마지막 DB 상태 + 남은 가정으로 계산한 영향. 실행 후 재분석에 실패하면 `null`로 두고 답변에 안내 |
| `remaining_whatif` | 실행으로 덮어쓴 발주 가정을 제거한 목록. 실행한 split을 다시 가정으로 더하지 않음 |
| `blocked_targets` | `"INV-001|MCU"` / `"INV-003|CASE-PLUG"`별 보류 이유 |
| `proposals` | proposal ID·revision·이전 제안/승인 ID·원본 대안·검증·승인 ID·상태·실행 결과 목록 |
| `approval_summary` | 완료 시 `EXECUTED`, `REJECTED`, `BLOCKED` 제안 수. 거절된 이전 revision도 이력으로 계산 |

한 작업에 승인 카드가 여러 번 생길 수 있다. **카드 하나의 승인은 그 카드의 tool·args 한 건에만 적용**한다.
10/21부터 최초 범위의 안들을 각각 검증·근거 확인하고 조합 검증 후 카드들을 등록한다. 다른 카드가 아직 미결정이어도 승인된 카드부터 실행한다.
같은 발주를 두 번 건드리는 안은 동시에 올리지 않는다. 각 실행 전 최신 후보·인자·비용·위험·영향을 다시 검사한다. 자동 신규 발주는 만들지 않는다.
`sources`와 승인 카드 `evidence`에는 검색 결과의 원문을 실제로 읽은
`{doc_id, title, clause_no, version, effective_date, uri, excerpt}`만 넣는다.

### 3.3 승인 흐름의 payload

```
agent ──▶ backend   { tool, args, summary, amount, evidence }
backend ──▶ 화면     위 + approvalId          (amount는 역할에 맞춰 마스킹)
화면 ──▶ backend     { approvalId, decision, reason }
backend ──▶ agent    { threadId, approvalId, decision, reason }
```

- `approvalId`는 **backend가 만든다.** agent는 이벤트를 보낼 때 그 값을 알 수 없다. (✅ 이벤트 응답 `{approvalId}`로 돌려주긴 한다)
- 거절은 `reason` 필수.
- 승인 카드에는 **변경 전후를 함께** 보여 준다. 도구 이름과 인자만으로는 사람이 판단할 수 없다.

✅ **구현 (10/20 밤, `Approvals.java`)**

승인 카드 (`GET /api/approvals`의 한 줄, `/api/approvals/{id}`):

```jsonc
{ "approval_id": "APR-8bd9fbd5", "task_id": "…", "tool": "split_po",
  "args": { "po_id": "PO-2609-001", "split_qty": 100, "new_due_date": "2026-11-25" },
  "summary": "…", "amount": 390000, "evidence": [],
  "problem": "스마트 벽스위치 생산(2026-11-23, PP-03)에 릴레이 400개 부족 · …",   // AI가 만든 고정된 품목별 문제
  "taskProblem": "증산으로 생긴 결품과 감산으로 생긴 과잉을 함께 해결",          // 구매가 입력한 작업 전체 설명
  "memo": "파워링크 담당자와 통화: 400개 선납 가능",                             // 요청자 메모 (6.33)
  "before": { "po_id": "PO-2609-001", "item_id": "MCU", "qty": 500, "due_date": "2026-12-12" },
  "after": [ { "po_id": "PO-2609-001", "qty": 400, "due_date": "2026-12-12" },
             { "po_id": "(신규)", "qty": 100, "due_date": "2026-11-25" } ],   // 실행 후엔 실제 새 번호로 바뀐다
  "status": "PENDING",                 // DRAFT → PENDING | WITHDRAWN, PENDING → APPROVED | REJECTED, APPROVED → EXECUTED.
                                       // 구매의 최초 요청은 DRAFT로 시작, 팀장 요청과 거절 뒤 재제안(41번)은 바로 PENDING. 남의 DRAFT·WITHDRAWN은 팀장에게도 안 보인다
  "requested_by": "buyer", "requested_at": "…", "decided_by": null, "decided_at": null,
  "reason": null, "executed_at": null }
```

- 요청자 역할이 BUYER·APPROVER가 아니면 카드를 만들지 않는다 → agent에 `403 ROLE_NOT_ALLOWED`.
- 같은 작업·도구·인자로 PENDING 카드가 있으면 새로 만들지 않고 그 `approvalId`를 준다 (agent 이벤트 재전송 대비). 화면에도 다시 보내지 않는다.
- 이 중복 제거는 **PENDING에만** 적용된다. AI는 카드 등록 HTTP 요청을 자동 재시도하지 않는다. 응답 유실/재시작으로 등록 여부가 모호하면 `REGISTRATION_UNKNOWN`으로 보류하며, 기존 카드 확인 없이 새로 만들지 않는다.
- 변경 도구는 승인이 `APPROVED`이고 **도구·인자가 카드와 정확히 같을 때만** 실행한다. 실행되면 `EXECUTED` — 같은 승인으로 두 번 실행할 수 없다.
- 변경 + 변경 이력 + 승인 `EXECUTED`는 **한 트랜잭션**이다 (AUD-001). 하나라도 실패하면 아무것도 안 바뀐다.
- 팀장 결정은 agent 재개와 묶지 않는다. agent가 죽어 있어도 결정은 저장되고, 재개 요청은 대기열에서 30초·2분 뒤 다시 보낸다. 3번 다 실패하면 화면에 `FAILED`(`AGENT_UNAVAILABLE`)가 간다 (decisions 6.24).

### 3.4 승인 정책

- **변경 도구는 금액과 무관하게 전부 승인을 받는다.** 임계값을 두지 않는다.
- 팀장은 자기가 요청한 건을 자기가 승인할 수 있다 (요청자 ≠ 승인자 규칙 없음).
- 승인 대기는 **만료되지 않는다.**
- 거절되면 agent가 사유를 반영해 다른 대안을 낸다. **2회**까지.
- 재제안 한도는 품목·규칙별로 적용한다. 후보는 기존 `compare_options` 결과에서만 선택한다. 거절 사유로 새 수량 후보를 만드는 기능은 이번 범위에서 제외한다.
- backend 승인 상태는 `PENDING/APPROVED/REJECTED/EXECUTED` 그대로다. `BLOCKED` 등은 AI 제안 상태로 요청자 SSE와 최종 결과에 표시한다. 다른 사람이 만든 작업의 SSE는 팀장도 구독할 수 없으며, 팀장은 승인 목록 갱신 및 작업 최종 결과 조회를 사용한다.
- 등록/실행 여부를 확정하지 못한 건은 `DONE` 처리하지 않는다. 다른 정상 카드의 결정·실행은 계속 처리한다. 업무 변경 응답이 불명확할 때는 journal을 보고 자동 재실행을 막는다.

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
| Harness 한도 | 일반 경로 8턴, ADJUST 기본 64턴(`MAX_ADJUST_TURNS`), 작업 실행 구간 기본 600초(`AGENT_WORK_TIMEOUT_S`), 그래프 600단계(`AGENT_RECURSION_LIMIT`), 같은 도구 3회·429/503 2회 재시도 | 다건 준비·재제안을 포함하도록 조정. 사람 승인 대기는 시간 제한에서 제외. 대상 최대 10개, 최초+재제안 최대 30개 |
| 통합 발주 (A4) | **MVP 제외** | 도구·계산·승인이 한 벌 더 필요한데 평가표에 해당 항목이 없다. 장표에 향후 과제로 |
| 자동 분석 (B1) | **넣는다** | 작업 생성 한 번이면 되고, SC4 시연이 완성되며 대기열 가산점과 이어진다 |
| 근거 문서 (C3) | **3종** — 구매 규정 / 협력사 계약 조건 / ECN 3~5건 | 통합 발주를 뺐으므로 통합 발주 기준 문서도 뺀다 |
| 문서 주소 | `doc://{doc_id}/{clause_no}` · 조항 단위 청킹 | 뼈대에 이미 들어 있다 |

근거는 [decisions.md 6절](decisions.md)에 적었다.

### Harness 관측 추가 계약 (2026-10-21)

- `TRACE` 이벤트는 업무 상태를 변경하지 않는다. 회차별 `traceId`, `previousTraceId`, `executionKind`, `outcome`, `traceUrl`, 실행 시간·호출·오류·복구·사용량 통계를 담는다.
- task 목록의 `trace`는 최신 회차, 상세 조회의 `traces[]`는 회차별 최종 기록이다. 기존 작업 조회 권한을 그대로 적용한다.
- `taskId`/`threadId`로 승인 전후를 묶고, 재개 command마다 새 root trace를 만든다. trace 정보는 업무 이벤트 중복 제거 키에 넣지 않는다.
- 토큰 수를 확보하지 못하면 null, LLM 호출이 없을 때만 비용 0이다. 실제 모델 비용은 LangSmith에서 확인하며 임의 단가 계산을 하지 않는다.
- 전체 기준·검증 결과·재현법: [harness-validation.md](harness-validation.md).

### 작업 단위 구매 검토 계약 (6.39)

- `TASK_REVIEW_READY {reviewId, approvalIds}`: agent가 이번 회차의 모든 카드 등록을 마치고 공통 결정 대기에 도달한 뒤 보낸다. 등록 결과가 불명확하면 보내지 않는다. `approvalIds`는 이전 회차를 포함한 등록 카드 ID다.
- `GET /api/ai/tasks/{id}`의 요청자 응답에 `reviewReady`가 붙는다. UI는 이 목록에 현재 초안이 모두 포함될 때만 전송한다. 최종 검증은 backend가 한다.
- 전송/철회 body: `{requestId, reviewId, taskProblem, memos: {approvalId: "개인 메모", ...}}`. `memos`의 키는 **현재 작업의 모든 DRAFT 카드 ID와 정확히 같아야** 한다. 개인 메모는 빈 문자열 허용, 문제 설명/메모 각 4,000자까지. `taskProblem`은 전송에 필수, 철회에는 선택이다.
- 응답: `{cards: [...]}`. 각 카드의 기존 `problem`은 그대로, `taskProblem`·`memo`는 분리해서 반환한다. 기존 일반 문자열 메모도 그대로 읽는다.
- 같은 `requestId`·내용 재전송은 멱등 처리한다. 다른 내용, 카드 일부 선택, 준비 중/새 카드 추가, 이미 처리한 회차는 409 `APPROVAL_NOT_PENDING`. 다른 요청자는 403이다. 동일 작업의 등록·검토는 작업 행 잠금으로 직렬화한다.
- 기존 `/api/approvals/{id}/submit|withdraw`는 400으로 작업 단위 검토를 안내한다. 카드별 우회 전송을 허용하지 않는다. 팀장의 `/api/ai/tasks/{id}/approval`은 기존 그대로 부품별이다.
- 저장: 새 컬럼 없이 `approval.memo`에 `{format:"task-review-v1", taskProblem, personalMemo, requestId, reviewId, decision}`을 JSON 문자열로 저장한다. 화면 API는 이를 해석하므로 화면에 원시 JSON을 표시하지 않는다. DB의 memo를 직접 읽는 별도 코드가 생기면 이 버전을 해석해야 한다.
- **6.40 변경:** 최초 제안만 DRAFT로 구매가 검토한다. 거절 후 재제안은 아래 검증을 통과하면 바로 PENDING으로 팀장에게 간다. 구매 화면이나 로그인 유지에 의존하지 않는다.
- `DONE.result`, 변경 도구 인자, 팀장 개별 승인 API는 유지한다. 입력 중 메모는 브라우저 메모리에만 있으며 새로고침하면 사라진다.

### 거절 후 직접 재제안 계약 (6.40)

- `APPROVAL_REQUIRED.previousApprovalId`: 이전에 거절된 승인 카드 ID. 최초 제안은 null. `proposalId`, `revision >= 2`와 함께 보낸다.
- backend는 이전 카드가 같은 작업·요청자·품목인지, 실제 REJECTED인지 확인한다. 조건을 만족한 재제안만 PENDING으로 등록한다. 기존 팀장의 승인 API·실행 검증은 유지한다.
- 전체 문제 설명과 이전 구매 메모는 이전 카드에서 읽는다. API의 `previousApprovalId`로 계승된 메모임을 구분하고, 화면은 "이전 요청의 구매 메모"로 표시한다. 최초 구매 전송의 requestId는 계승하지 않는다.
- 같은 previousApprovalId + proposalId로 재등록하면 기존 카드를 반환한다. 이미 결정·실행된 카드도 되돌리거나 중복 생성하지 않는다.
- 업데이트 전 등록된 재제안 초안은 agent의 기존 연결 기록을 바탕으로 이벤트에 `approvalId`까지 넣어 복구한다. backend는 ID·작업·요청자·도구·인자·이전 요청 연결을 확인하고 해당 DRAFT만 PENDING으로 전환한다. 이미 결정된 카드의 상태는 유지한다.
- 복구 완료 여부는 기존 agent 연결 기록의 JSON에 남긴다. 새 테이블·컬럼·마이그레이션은 없다. 응답이 유실되면 같은 기존 카드 ID로 재시도한다. 등록 결과가 불명확한 UNKNOWN 카드는 자동 재등록하지 않는다.
- 승인 대기함의 연두색 작업 카드에는 원 질문을 표시하지 않는다. 전체 문제 설명이 없는 이전 기록은 질문으로 대체하지 않고 미작성 안내를 표시한다.
