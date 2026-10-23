# 생산·구매 부서 요청 구현 및 검증

작성일: 2026-10-22 · DB 스키마 변경 없음

## 1. 반영한 흐름

| 경우 | 처리 순서 | 실제 변경 시점 |
|---|---|---|
| 생산 → 구매 | 생산 담당자의 영향 분석 → 변경 전후·문제·요청 내용 확인 및 동의 → 구매 담당 접수 → AI 해결안 → 구매 검토·메모 전송 → 팀장 개별 검토 → 전체 실행 | 팀장 전체 실행 시 발주와 동의한 생산계획을 같은 트랜잭션으로 변경 |
| 구매팀장 최종 거절 | 부품별 AI 재요청과 별도로 요청 전체 최종 거절 → 사유 필수·확인 팝업 → 생산 요청자에게 결과 알림 | 발주·생산계획 변경 없음. 미실행 승인 카드는 철회 |
| 구매 → 생산 | 구매 담당자가 확정된 협력사 지연 입력 → 자동 영향 분석 → 생산 조정 요청 → 생산 담당 접수 → AI 분석 또는 수량·날짜 직접 입력 → 변경안 검증 → 확인 후 저장 | 지연은 사실 입력 단계에서 저장. 생산계획은 담당 생산관리자가 확인한 시점에 함께 저장 |
| 구매 채팅 → 생산 (10/23 추가) | 채팅으로 입고 지연 분석 → 생산팀 요청 버튼 → 대상 발주·입고일·문제·사유 확인 및 저장 동의 → 생산 담당 접수·검토 | 채팅 분석은 미저장. 확인된 전송 시 입고일·이력·사실·부서 요청을 함께 저장. 생산계획은 담당자 확정 시 저장 |
| 생산 조정 불가 | 담당 생산관리자가 사유를 적어 최종 거절 → 구매 요청자에게 결과 알림 | 이미 저장한 입고 지연 유지. 생산계획은 변경하지 않음 |
| 구매 → 생산 → 추가 구매 필요 | 받은 요청에서 생산 변경안을 분석하고 구매 요청으로 연결 → 구매 검토·팀장 전체 실행 | 원래 생산 요청과 연결 구매 요청을 함께 완료. 구매 요청 거절·취소 시 원래 요청은 사유와 함께 생산 재검토로 복귀 |

`AI 재요청`, `대안 없음`, `요청 최종 거절`은 서로 다르다. 대안이 없어도 요청을 자동으로 최종 거절하지 않는다. 요청함에 담당자 확인 필요 상태를 표시하고, 해결안 카드가 없는 경우에도 구매 담당자가 팀장에게 최종 검토를 요청할 수 있다.

생산 담당자의 전송 동의는 **그때 확인한 계획의 날짜·수량**에 한정한다. 구매 담당자의 후속 질문으로 동의 내용을 바꾸거나 두 번째 실행 작업을 만들 수 없다. 조건을 바꾸려면 새 분석·새 요청이 필요하다.

## 2. 화면에서 확인하는 방법

현재 개발 컨테이너는 이번 테스트에서 재시작하지 않았다. 저장된 로컬 변경을 개발 화면에 적용하려면 저장소 루트에서 실행한다. 기존 개발 DB 볼륨 삭제는 필요하지 않다.

```bash
docker compose up -d --build backend agent frontend
```

시연 계정은 기존 seed의 `planner`, `buyer`, `approver` / 비밀번호 `1234`. 아래 예시는 **시드 상태**를 기준으로 한다. 이미 변경한 DB에서는 현재 계획·발주 값을 먼저 확인한다.

### 생산 → 구매 → 전체 실행

1. 생산관리 계정으로 AI에게 `스위치 11/23 생산 수량을 600개에서 900개로 늘리고, 플러그 11/30 생산 수량을 800개에서 500개로 줄이면 문제 있어?` 질문.
2. 분석 완료 후 **구매팀에 해결 요청**을 열어 계획 변경 전후, 현재 문제, 요청 내용을 확인하고 동의 후 전송.
3. 구매 계정의 **부서 요청**에서 접수하고 AI에게 해결 방법을 질문. 기존처럼 전체 문제 설명·품목별 메모를 작성해 팀장에게 전송.
4. 팀장 계정에서 각 제안을 검토 완료 처리. 이 단계에서는 발주·생산계획이 바뀌지 않아야 한다.
5. 작업 전체 **승인 및 실행** 팝업에서 발주와 생산계획 변경을 함께 확인. 취소하면 변경 없음. 실행하면 발주와 PP-03 900개·PP-04 500개가 함께 반영되어야 한다.
6. 생산관리 계정의 부서 알림·요청함에서 처리 완료 확인. 구매 담당자의 개인 질문·대화는 생산관리 작업 이력에 나타나지 않아야 한다.

### 최종 거절

1. 새 요청을 구매에서 팀장에게 전송하거나, 해결안이 없다면 요청함의 팀장 검토 요청 사용.
2. 팀장이 요청 상세에서 사유 `협력사 발주 변경 불가`를 적고 **요청 최종 거절** 확인.
3. 생산 담당자에게 최종 거절·사유가 표시되고 새로고침 후에도 유지되는지 확인. 발주·생산계획은 변경되지 않아야 한다.
4. 부품 하나의 **AI에게 재요청**은 계속 기존 재제안 흐름을 사용하며 요청 전체를 최종 거절하지 않는다.

### 구매 → 생산

1. 구매 계정의 **발주·입고**에서 PO-2610-106을 열고 **협력사 확정 지연**으로 2026-11-27, 사유를 저장.
2. 자동 분석 완료 후 생산팀에 조정 요청 전송. 이 시점에 입고 지연은 이미 실제 데이터이다.
3. 생산 계정에서 접수하고 `스위치 11/23 생산을 11/28로 미루면 문제 있어?` 질문.
4. 요청의 생산 변경 검토 화면에서 AI가 제시한 날짜를 확인하거나 직접 수정. 검증 통과·사유 작성 후 확인 팝업에서 생산계획 변경 확정.
5. 구매 요청함의 완료 알림과 PP-03 변경 확인. 별도의 새 요청을 최종 거절한 경우에는 입고 지연만 남고 생산계획이 유지되는지 확인.

부서 알림은 앱 내부 알림이다. 화면이 열려 있으면 5초 간격과 창 포커스 복귀 시 갱신하며, 읽음 상태는 서버에 사용자별로 저장한다. 다른 사용자의 원본 채팅을 공유하지 않고 명시적으로 전송한 요청 정보만 공유한다.

## 3. 변경 파일

| 영역 | 파일 | 변경 내용 |
|---|---|---|
| 요청 저장·권한 | `backend/.../CoordinationStore.java` (신규) | 기존 task_event의 요청·연결·읽음 상태, 담당자 권한, 요청 잠금, 연결 요청 결과 전파 |
| 요청 API | `backend/.../CoordinationRequests.java` (신규) | 전송 미리보기·생성·목록·상세·접수·읽음·팀장 검토 요청·최종 거절 |
| 생산 확정 | `backend/.../CoordinationExecution.java` (신규) | 생산 담당자 전용 변경안 미리보기와 재검증·일괄 저장 |
| 공통 업무 검증 | `backend/.../BusinessSnapshot.java`, `ProductionPlanChanges.java` (신규) | 업무 데이터 잠금·fingerprint, 계획 변경 형식·대상 검증, 변경 전후·이력 저장 |
| AI 작업 접수 | `backend/.../Tasks.java` | 인증된 요청 컨텍스트 전달, 동일 요청의 해결 작업 중복 방지, 연결 후속 질문, 메타 이벤트와 SSE 분리, 종료 요청 차단 |
| 기존 승인 연동 | `backend/.../Approvals.java`, `TaskBatchExecution.java` | 전송·실행 전 요청 상태 확인, 동의 내용 검증, 발주+생산계획 원자 실행, 최종 거절 상태 표시 |
| 입고 지연 사실 | `backend/.../ScreenApi.java` | 기존 지연 저장 후 신뢰 가능한 사실 이벤트 기록. 실제 JWT 역할과 금액 마스킹용 분석 역할 구분 |
| Agent 컨텍스트 | `agent/main.py`, `whatif.py`, `graph.py` | 전송된 whatif 고정, 권한별 분석·해결 경로, 읽기 전용 이어 묻기, 실제 저장한 계획을 최종 답변에 표시 |
| Agent 종료 동기화 | `agent/task_batch.py`, `approval_runner.py` | 서버의 최종 거절·취소와 중단된 승인 흐름 동기화, 종료 후 재등록·실행 방지 |
| 알림·요청함 | `frontend/src/services/coordination.ts`, `stores/coordination.ts`, `views/CoordinationRequests.vue` (신규) | 요청 목록·담당 접수·알림·연결 관계·결과·새 요청 시작 |
| 요청·생산 검토 UI | `components/CoordinationRequestForm.vue`, `PlanRequestReview.vue`, `RequestDecision.vue` (신규) | 공유 내용 확인·명시 동의, 생산 변경안 준비·검증·확정, 사유 있는 최종 거절 |
| 기존 UI 연동 | `App.vue`, `main.ts`, `components/AiPanel.vue`, `ApprovalConfirm.vue`, `TaskBatchActions.vue`, `ProposalReview.vue` | 메뉴·경로·채팅 컨텍스트·전송 버튼·확인 팝업·계획 변경 전후·종료 상태 |
| 기존 데이터·입고 UI | `services/task-batch.ts`, `stores/workspace.ts`, `views/Purchasing.vue` | 종료 상태 타입·대기 명령 해제, 확정 지연 입력과 자동 분석 진입 |
| 테스트 | `backend/.../CoordinationRequestsTest.java`, `frontend/tests/coordination.test.ts`, `tests/test_coordination.py`, `integration_coordination.py`, `browser_coordination.cjs`, `compose.coordination-ui.yml` | 권한·동시성·원자성·양방향 HTTP·실제 브라우저 검증 |

위 `backend/...`는 `backend/src/main/java/capstone/`, 테스트는 `backend/src/test/java/capstone/`이다. 프론트 `components/`, `services/`, `stores/`, `views/`는 `frontend/src/` 아래이다. 일반 생산계획 화면 `Plans.vue`, MCP 도구, RAG 문서·검색, DB DDL·시드는 변경하지 않았다. 받은 요청의 생산 변경은 별도 검토 화면에서 수행한다.

## 4. API와 상태 계약

모든 `/api/coordination/**` 경로는 로그인 필요. 접근 가능한 응답에 기존 역할별 금액 마스킹 적용.

| 메서드·경로 | 입력 | 동작 |
|---|---|---|
| GET `/api/coordination/preview?taskId=...` | 본인 완료 IMPACT 작업 | 현재 업무 기준 재계산, planOps·planChanges·facts·analysis·fingerprint 반환 |
| POST `/api/coordination/requests` | sourceTaskId, expectedFingerprint, commandId, message, 선택 parentRequestId | 요청 생성. 생산→구매는 전송한 planOps에 대한 생산 담당자의 동의 기록 |
| GET `/api/coordination/requests` / `/{id}` | — | 요청자·받는 부서·전송받은 팀장에게만 공유 요청 조회 |
| POST `/{id}/claim` | expectedRevision | 받는 부서 한 명이 담당 접수 |
| POST `/{id}/read` | revision | 로그인 사용자별 읽음 버전 저장 |
| POST `/{id}/escalate` | expectedRevision | 담당 구매가 카드 없이도 팀장 최종 검토 요청 |
| POST `/{id}/reject` | expectedRevision, commandId, reason | 방향에 맞는 최종 결정자만 요청 전체 거절 |
| POST `/{id}/plan-preview` | expectedRevision, whatif | 받은 생산 요청의 변경안 검증·previewId 발급. 실제 계획은 미변경 |
| POST `/{id}/plan-execute` | expectedRevision, commandId, previewId, reason | 담당 생산관리자의 확인된 변경안을 한 트랜잭션으로 저장 |
| POST `/api/ai/tasks` | 기존 question + 선택 coordinationId | 담당자만 연결 분석 시작. 구매 해결 작업은 요청당 하나 |
| GET `/internal/tasks/{id}/coordination` | 기존 Agent 서비스 토큰 | Agent의 종료 상태 동기화용. 브라우저의 권한 근거로 사용하지 않음 |

상대 경로 `/{id}/...`의 전체 접두사는 `/api/coordination/requests`이다.

- 요청 상태: `OPEN` → `IN_PROGRESS` → `WAITING_REVIEW` → `COMPLETED` / `REJECTED` / `CANCELLED`.
- 역방향은 생산 담당자가 `IN_PROGRESS`에서 검증·확정한다. 추가 구매 요청을 보낸 동안 `WAITING_LINKED`로 두고 독립 확정·최종 거절을 막는다.
- 동시 접수는 한 담당자만 성공. 전송·최종 거절·계획 확정은 같은 명령 ID와 내용의 재시도에 중복 반영하지 않는다.
- `DONE.result`의 기존 `{intent, whatif, impact}` 유지. 연결 결과에는 `coordinationId`, `plan_changes`를 추가한다. 계획까지 저장했다면 `remaining_whatif=[]`.
- 기존 ai_task 상태 열은 변경하지 않는다. `COORDINATION_REQUEST`, `COORDINATION_LINK`, `COORDINATION_READ`, `COORDINATION_PLAN_PREVIEW`, `COORDINATION_DECISION`, `COORDINATION_START`, `SUPPLIER_DELAY_FACT`는 기존 task_event에 저장한다. 내부 메타 이벤트는 SSE 채팅 이벤트로 노출하지 않는다.
- 요청 생성·권한·지연 사실은 백엔드에서만 생성한다. Agent 일반 이벤트 콜백으로 이를 위조할 수 없다.

## 5. 실행 안전장치

1. 최신 업무 데이터에서 계획 변경과 선택한 발주 변경을 함께 검증한다. 원래 요청의 긴급 결품·과잉을 해소하고 다른 결품·과잉을 새로 만들거나 악화시키면 실행을 막는다. 연결된 원래 요청의 문제도 검증 범위에 포함한다.
2. 생산 담당자가 확인한 계획의 변경 전 값·동의한 whatif를 다시 확인한다. 도중에 다른 사용자가 그 계획을 바꾸면 새 요청으로 검토하도록 차단한다.
3. 미리보기 이후 업무 fingerprint가 바뀌면 실행을 차단한다. 업무 테이블 잠금과 트랜잭션 안에서 변경·사후검증을 수행한다.
4. 발주·생산계획·승인 결과·변경 이력·요청 완료는 함께 커밋한다. 중간 계획 저장 실패 시 앞선 발주 변경도 원복한다.
5. 최종 거절과 실행이 경쟁하면 먼저 요청 잠금을 획득해 완료한 한쪽만 성공한다. 종료 후 늦게 도착한 승인 카드와 실행도 차단한다.

## 6. 검증 범위와 남은 한계

- 백엔드: 기존 검사 포함 69개 통과. 신규 14개는 역할·사생활·동시 접수·중복 명령·오래된 동의·전체 원복·최종 거절과 실행 경쟁·연결 요청 완료 및 복귀를 검사한다.
- 프론트: 기존 검사 포함 53개 통과. TypeScript 검사와 프로덕션 빌드 통과.
- Python: RAG·승인 러너·전체 실행·Harness·부서 요청 71개, 읽기 도구·이어 묻기 10개로 총 81개 통과.
- 기존 HTTP 시나리오: 골든셋, SC1~4, 재제안, 작업 검토·전체 실행, 입고 수동 수정, 본인 이력 제한, 이어 묻기 통과.
- 신규 HTTP: 생산→구매 완료, 팀장 최종 거절, 구매→생산 완료, 생산 최종 거절, 연결 요청 동시 완료, 연결 요청 거절 후 복귀까지 6개 흐름 통과.
- 신규 HTTP·브라우저 및 Python 검사 재현 명령은 [tests/README.md](../tests/README.md)의 부서 요청 검사를 따른다. 브라우저에서는 전송 동의, 알림·담당 접수, 검토 후 무변경, 실행 팝업 취소, 실제 계획 저장, 최종 거절 사유 재조회, 역방향 변경안을 확인했다.
- 이번 워크플로 검증은 **가짜 모델·테스트 임베딩**을 사용한 격리 DB에서 실제 백엔드·Agent·MCP·큐·프론트를 연결했다. 실제 LLM의 문장별 추출 품질·대안 탐색 품질을 새로 평가한 결과는 아니다.
- 판단 범위는 현재 영향 계산기가 지원하는 긴급 결품·과잉이다. 생산능력·설비·인력·고객 납기까지 최적화하지 않는다. 비교 대안 생성 로직과 시연 기준일은 기존 그대로이다.
- 업무 전체 fingerprint와 테이블 잠금은 보수적인 MVP 보호 장치이다. 관련 없는 변경도 재검증을 요구할 수 있고, 데이터·동시 사용량이 커지면 대상별 버전 관리가 필요하다.
- 요청 목록은 기존 이벤트에서 현재 상태를 조회한다. 대규모 운영용 페이지네이션·전용 요청 테이블·알림 전달 큐는 추가하지 않았다. 요청 알림은 앱 내부 조회 방식이며 사내 메신저·이메일 연동은 없다.
- 실행이 완료된 요청은 자동 역실행하지 않는다. 전체 취소는 아직 실행하지 않은 변경안을 철회하는 동작이다. 확정 지연을 되돌리려면 별도의 실제 업무 변경이 필요하다.

개발 DB·기존 컨테이너와 분리하여 검사했다. 변경 사항은 사용자 요청에 따라 Git 저장소에 반영한다.


## 7. 구매 채팅에서 입고 지연 전송 (10/23)

초기 seed 기준 테스트 질문:

> 11/13 와이파이칩 입고일이 일주일 늦어졌어. 문제있나?

1. 구매 계정(`buyer`)의 새 대화에서 질문한다. `PO-2609-002`의 기존 입고일 2026-11-13에 7일을 더해 2026-11-20으로 분석한다. 이때 실제 입고일은 그대로이다.
2. 결과의 **생산팀에 일정 조정 요청**을 누른다. 전송 창에 발주·변경 전후 날짜·현재 재계산한 문제와 요청 내용이 채워진다. 닫기만 하면 저장하지 않는다.
3. **지연 사유**를 입력하고 실제 입고일 저장·전송에 동의한 뒤 **지연 사실 반영 및 생산팀에 전송**을 누른다.
4. 생산 계정(`planner`)의 부서 요청함에서 접수한다. **AI에게 생산 변경안 묻기**로 `플러그 11/16 생산을 11/21로 미루면 문제 있어?`를 질문한다.
5. **이 생산계획 변경안 검토 →**에서 검증하고 사유를 작성한 뒤 확정한다. 구매 요청함에 처리 결과가 표시된다. 생산팀의 최종 거절은 이미 확인해 저장한 입고 지연을 취소하지 않는다.

### 추가 계약

- `list_receipts` 결과에 `item_name` 추가. 지연 질문에만 OPEN 발주 목록을 분류 컨텍스트로 전달하며 가격은 포함하지 않는다.
- 분류 `receipt_delays[]`: `po_id` 또는 `item_name`, 선택 `original_due_date`, `delay_days` 또는 `new_due_date`. 원래 입고일에 대한 날짜 계산은 코드가 수행한다. 같은 부품·원래 날짜에 발주가 여러 개면 후보 번호를 제시한다.
- `DONE.result.receipt_delay = {reported, changes:[{poId,itemName,before,after}]}`. 미저장 분석 결과이며, `intent/whatif/impact` 계약은 유지한다. `before/after`에는 PO ID·품목·수량·입고일·상태만 둔다.
- 전송 미리보기 `requiresDelayConfirmation=true`이면 생성 요청에 `confirmDelay:true`, 공백이 아닌 `delayReason`을 추가해야 한다. 기존 `message`, `expectedFingerprint`, `commandId`도 유지한다.
- 서버는 본인 완료 IMPACT 작업, BUYER 권한, 확정 통보 메타데이터, 순수 입고일 지연 whatif, 유일한 OPEN 발주, 분석 당시 before/after 일치를 검사한다. 긴급 결품·과잉이 있어야 전송한다.
- 전송 확인 시 PO 날짜·이력·신뢰 가능한 `SUPPLIER_DELAY_FACT`·`COORDINATION_REQUEST`가 한 트랜잭션으로 저장된다. 기존 단일 fact와 새 `facts[]` 형식을 함께 읽는다. 역방향 요청의 `planOps`는 빈 목록이다.
- 가정·추정·가능성 표현은 분석만 수행한다. `늦어지면?`, `늦어질 수 있다`, `약 1주` 등을 확정 사실로 저장하지 않는다. 한 작업에서 가정과 확정 지연을 섞으면 자동 전송 대상으로 취급하지 않는다. 1·4·12주 비교 생성은 별도 개발 범위이다.

### 변경 파일

| 영역 | 파일 | 내용 |
|---|---|---|
| AI | `agent/receipt_delay.py`, `whatif.py`, `graph.py`, `prompts/classify_receipt_delay.md`, `eval_golden.py` | 실제 발주 해석, 상대 기간 계산, 모호성·가정 구분, 분석 스냅샷, 기존 평가 경로 연동 |
| Backend | `SupplierDelays.java`, `CoordinationRequests.java`, `ScreenApi.java`, `InternalApi.java` | 공통 사실·이력 저장, 확인된 채팅 전송의 원자 처리, 동시 변경·중복 검사, 품목명 조회. DB DDL 변경 없음 |
| Frontend | `AiPanel.vue`, `CoordinationRequestForm.vue`, `services/coordination.ts` | 전송 버튼, 변경 전후·자동 채운 요청 내용, 지연 사유·저장 동의 |
| 검증 | `test_receipt_delay.py`, `integration_chat_delay.py`, `browser_chat_delay.cjs`, `check_chat_delay_model.py`, 기존 Java·프론트 테스트 | 날짜·권한·저장 시점·중복·롤백·실제 HTTP·브라우저·실제 모델 추출 |

### 재현 명령

아래 프로젝트는 개발 DB와 별도 볼륨을 사용한다. 통합 테스트는 `capstone_ai_test` DB의 데이터를 초기화한다.

```bash
docker compose -p capstone-chat-delay-test -f tests/compose.yml -f tests/compose.coordination-ui.yml up -d --build --wait
docker compose -p capstone-chat-delay-test -f tests/compose.yml run --rm tests python -u integration_chat_delay.py
# 위 통합 검사가 끝나면 seed 상태가 유지되어 브라우저 검사를 이어 실행할 수 있다.
PLAYWRIGHT_MODULE=/설치경로/playwright-core node tests/browser_chat_delay.cjs
# 실제 모델 추출만 검사할 때: demo 질문·발주 목록이 OpenAI API로 전송되며 비용 발생.
# 입고 변경을 실행하기 전의 seed 상태에서 실행한다.
env -u OPENAI_API_KEY -u OPENAI_MODEL docker compose --env-file .env -p capstone-chat-delay-test -f tests/compose.yml -f tests/compose.real.yml run --rm --no-deps tests python -u check_chat_delay_model.py
# 이번 테스트 프로젝트와 그 테스트 데이터만 제거
docker compose -p capstone-chat-delay-test -f tests/compose.yml -f tests/compose.coordination-ui.yml --profile tools down -v
```

실제 모델 검사는 확정 통보·가정·모호한 발주 3개 질문의 분류·날짜 추출만 수행한다. 전체 연결 검사는 실제 API·DB·MCP·큐와 규칙 모델로 수행하며, 둘을 구분해 결과를 기록한다.


10/23 검증 기록:

- 백엔드 전체 75건 통과. 변경 후 입고·부서 요청 관련 21건 재확인.
- 프론트 58건 및 TypeScript/프로덕션 빌드 통과.
- AI 날짜/분류·Harness·승인·부서 요청 및 기존 이어 묻기 테스트 통과.
- 신규 채팅 통합 2개, 기존 양방향·거절·연결 통합 6개, 실제 Chrome 전송·취소·생산 접수 통과.
- 실제 모델 추출 3문항 통과(총 4회 호출): 확정·가정 각 1회, 발주가 모호한 경우 2회 후 되묻기. 전체 실모델 E2E를 수행한 결과는 아님.
- Docker 최초 빌드 성공 후 재빌드에서 Maven 일부 의존성 조회 실패, 로컬 JAR 패키징에서도 플러그인 클래스 누락 발생. 최종 백엔드 통합 검증은 최초 테스트 이미지의 JAR에 Maven 테스트를 통과한 최종 `SupplierDelays.class`를 교체한 별도 테스트 이미지로 수행. 저장소의 의존성 버전·Dockerfile은 변경하지 않음. 배포 환경의 정규 재빌드 성공 여부는 별도 확인 필요.


### main 날짜 검사와의 병합

- `main`의 질문에 없는 날짜 차단(결정 6.49)과 구매 채팅 전달(결정 6.50)을 함께 유지한다.
- 질문에 명시한 지연 일수·주수와 실제 OPEN 발주의 기존 입고일로 계산한 변경만 날짜 검사에서 제외한다. 같은 계산 날짜를 다른 생산계획에 임의로 넣거나, 모델이 기간 자체를 지어내는 경우는 차단한다.
- 일반 생산 날짜 검사, 명시 날짜 표기 3종과 이어 묻기의 기존 가정 처리는 유지한다.
- 병합 후 재검증: 네트워크 없는 단위 74건 통과·조건부 3건 스킵, 신규 채팅 HTTP 통합 2개와 기존 양방향·최종 거절·연결 요청 통합 6개 통과. 병합된 Agent 이미지 빌드 성공. 이번 병합 검증에서 실제 LLM 호출·백엔드 재빌드는 수행하지 않았다.
