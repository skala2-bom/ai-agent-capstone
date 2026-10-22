# Task 단위 검토·전체 실행 변경 기록

2026-10-22 · 로컬 구현. DB 테이블·컬럼·마이그레이션 변경 없음.

## 사용자 흐름

1. 구매 담당자는 기존처럼 전체 문제 설명과 품목별 메모를 작성해 task를 전송한다.
2. 팀장은 각 제안의 **승인**을 눌러 `✓ 검토 완료`로 표시한다. 발주·입고 데이터는 바뀌지 않는다. 체크를 다시 누르면 검토를 해제한다.
3. **수정 필요**를 누르면 원안은 `✕ 실행 제외`가 된다.
   - **AI에게 재요청**: 사유 필수. 해당 품목만 재탐색하고 새 제안을 팀장에게 직접 전달한다. 새 제안은 다시 검토한다.
   - **대안 없음**: 결과와 다음 행동을 표시하고 검토 상태를 유지한다. 직접 수정으로 이어갈 수 있다.
   - **직접 수정**: 해당 발주·입고 상세에서 수량·입고일·사유 입력 → **수정안 확정**. 새 제안으로 등록되며 실제 업무 데이터는 아직 바뀌지 않는다.
4. 활성 제안이 모두 ✓/✕로 정리되면 task의 **승인 및 실행**을 선택할 수 있다. 최종 확인 팝업을 거친다.
5. ✓ 제안을 한꺼번에 가정하여 원래 task의 결품·과잉 문제가 해결되는지 검사한다. ✕ 품목의 문제도 검증 대상에서 빠지지 않는다. 다른 생산에 결품·과잉을 새로 만들거나 키워도 보류한다.
6. 통과하면 선택한 변경·승인 상태·이력을 하나의 DB 트랜잭션으로 저장한다. 중간 실패 시 전체 원복한다.
7. **전체 취소**는 아직 실행하지 않은 제안·수동 수정안을 철회한다. 이미 완료한 task를 과거 상태로 되돌리는 기능은 아니다.

일반 발주·입고 화면의 **입고 정보 저장**은 기존처럼 즉시 저장한다. task에서 **직접 수정**으로 이동한 경우만 수정안으로 등록한다.

## 변경 파일

| 위치 | 변경 내용 |
|---|---|
| `agent/task_batch.py` (신규) | 검토 상태, 최신 제안, 수동 수정안, task 실행·취소 명령, 응답 유실 재확인 |
| `agent/graph.py` | v3 개별 실행 금지, 대안 없음 후 대기 유지, 전체 실행 결과 반영, `batch_idle` 체크포인트 경로 |
| `agent/approval_runner.py` | 기존 영속 접수함에 BATCH 연결, 최초 업무 스냅샷·상태 발행 |
| `agent/main.py` | 신규 작업 v3, batch 명령 API, 동일 명령 재시도 |
| `backend/.../TaskBatchExecution.java` (신규) | 팀장 권한·검토 버전 확인, 인증된 명령 기록, 전체 재검증·트랜잭션·중복 실행 방지 |
| `backend/.../TaskEventLog.java` (신규) | 기존 task_event 사용, task 행 잠금으로 순번 충돌 방지, 체크포인트 순서로 최신 상태 조회 |
| `backend/.../Tasks.java` | RabbitMQ batch 전달, 기존 개별 승인 API의 v3 실행 차단 |
| `backend/.../Approvals.java` | 수동 카드·비용 계산, v3 개별 실행 차단, task 취소 이력 조회 |
| `backend/.../InternalApi.java` | 전체 트랜잭션 안에서 수량·날짜 동시 변경. 별도 MCP 변경 도구 추가 없음 |
| `frontend/.../ProposalReview.vue` (신규) | ✓/✕, 승인·수정 필요, AI 재요청·직접 수정 공통 UI |
| `frontend/.../TaskBatchActions.vue` (신규) | 전체 실행·취소, 검증 오류, 실행 결과 재확인 |
| `frontend/.../task-batch.ts` (신규), `workspace.ts` | 타입·상태 조회·명령·중복 클릭·오래된 응답 방지 |
| `Approvals.vue`, `AiPanel.vue`, `ApprovalConfirm.vue` | 승인함·AI 패널 공통 UI, 목적별 확인 팝업 |
| `Purchasing.vue`, `api.ts`, `types.ts`, `sse.ts` | task 수동 수정 진입, 변경 전후 표시, 상태 이벤트 계약 |

검토 상태는 기존 Agent PostgreSQL 체크포인트에 저장하므로 새로고침 후 유지된다. 백엔드는 인증·전달·원자 실행에 필요한 범위를 수정했고, DB 구조는 유지한다.

## API·상태 계약

- `GET /api/ai/tasks/{id}/batch`: 요청자 또는 전달받은 팀장이 조회. 구매가 보내지 않은 초안·철회 건은 팀장에게 비노출.
- `POST /api/ai/tasks/{id}/batch`: 팀장 전용. `requestId`, `expectedRevision`, `action`, 선택적인 `approvalId`, `reason`, `args`.
  - action: `CONFIRM`, `UNCONFIRM`, `NEEDS_EDIT`, `REPLAN`, `MANUAL`, `EXCLUDE`, `EXECUTE`, `CANCEL`.
  - MANUAL args: `po_id`, `new_qty`, `new_due_date`. 부모와 동일 발주만 허용.
  - REPLAN/MANUAL은 사유 필수. 실행자 ID는 JWT에서 지정.
  - `202`는 접수이며 완료가 아니다. 상태의 `lastRequestId`·`status`로 확인한다.
  - 같은 requestId는 같은 내용으로만 재전송한다. 오래된 검토 버전은 반영하지 않는다.
- 내부 `/internal/tasks/{id}/batch/{snapshot,reject,complete}`: Agent 서비스 토큰 사용. reject/complete는 별도로 저장된 팀장 명령이 있어야 한다.
- `TASK_BATCH_CONTEXT`, `TASK_BATCH_COMMAND`, `TASK_BATCH_STATE`, `TASK_BATCH_RESULT`를 기존 task_event에 기록한다.
- `TASK_BATCH_STATE.stateVersion`은 체크포인트 step. HTTP 도착 순서와 무관하게 최신 상태를 유지한다. `revision`은 동시 검토 변경 검사에 사용한다.
- `approval.status`는 체크만으로 APPROVED가 되지 않는다. 체크는 `manager_reviews`에 저장하고 전체 실행 트랜잭션에서 승인·실행 상태를 바꾼다.
- `DONE.result.intent/whatif/impact` 계약 유지. 실행 후 분석은 `post_execution_impact`, 실행 결과는 `executions`. 질문의 생산계획 가정 자체를 저장하지 않는 기존 동작도 유지한다.

## 보호 장치·현재 범위

- 최초 제안 등록 시 업무 데이터 fingerprint를 저장한다. 실행 전에 달라졌으면 아무 변경도 실행하지 않고 새 분석을 안내한다.
- 관련 업무 테이블의 짧은 쓰기 잠금 안에서 검증·저장·저장 후 검증을 수행한다. 이 트랜잭션 안에는 LLM/MCP/외부 HTTP 호출이 없다.
- 동일 task에서 같은 발주를 여러 번 바꾸면 실행을 보류한다. 다른 task가 먼저 실행해도 뒤의 task는 기존 분석으로 실행할 수 없다.
- 응답 유실·중복 클릭 시 같은 requestId의 저장 결과를 반환한다. 화면의 결과 재확인은 중복 실행하지 않는다.
- **보수적인 감지**: 현재 `item`, `inventory`, `production_plan`, `purchase_order`, `bom`, `supplier_metric` 전체를 비교한다. 관련 없는 품목 변경도 실행을 막을 수 있다. 대상별 버전 관리로 범위를 좁히는 것은 후속 개선 사항이다.
- **검증 범위**: 기존 영향 분석의 대상 결품(`orderable=false`)·과잉 및 다른 생산의 신규·악화 결품·과잉. 협력사의 실제 확약처럼 시스템에 없는 사실까지 보증하지는 않는다.
- **기존 작업**: v2 체크포인트는 기존 개별 승인 흐름을 유지한다. 새 흐름은 변경 후 생성한 task부터 적용한다.
- 승인 등록 자체가 불확실하면 기존 정책대로 자동 재등록하지 않고 확인 필요 상태로 둔다. 큐·서비스 장기 장애의 운영 복구는 별도이다.

## 검증

외부 LLM 호출 없이 별도 `capstone-task-batch-test` 프로젝트·`capstone_ai_test` DB에서 검사했다. 개발 컨테이너·DB는 초기화하지 않았다.

| 검사 | 확인 사항 |
|---|---|
| Backend | 체크만으로 미실행, 기존 개별 실행 차단, 중복 실행 방지, 두 번째 변경 실패 시 첫 변경·이력·상태 원복, 제외 문제·외부 수정 차단, 취소 조회, 수동 비용, 역순 이벤트 |
| Agent | 검토·수동 재제안·대안 없음·취소·응답 유실 및 기존 체크포인트/하네스 회귀 |
| SC1~4 HTTP 통합 | 로그인 → 실제 backend/queue/agent/MCP/RAG → 검토 → 전체 실행 → 이력 → SSE |
| `integration_task_batch.py` | 제외 문제 잔존, 대안 소진 후 수동 수정, 오래된 검토, 동일 발주의 task 충돌, 취소·중복 실행 |
| 실제 Chrome | 수동 수정안 등록 후 업무 미변경, 체크 후 미변경, 새로고침 체크 복원, 팝업 취소·Escape, 전체 실행, 브라우저 오류 없음 |

명령은 [tests/README.md](../tests/README.md). 실제 LLM의 대안 탐색 품질은 이번 검증 범위에 포함하지 않았다.

## 로컬 확인

프로젝트에서 DB를 지우지 않고 세 서비스만 다시 빌드한다.

```bash
docker compose up -d --build backend agent frontend
```

새 질문 예시: **11/23 스위치 생산을 900대로 늘리고 11/30 플러그 생산은 500대로 줄이면 어떻게 돼? 결품과 과잉 해결 방법 만들어 줘.**

박구매로 전체 문제 설명을 작성해 전송 → 정팀장으로 일부만 체크하여 미실행 확인 → 수정 필요/AI 재요청/직접 수정 → 모두 검토한 뒤 task 전체 실행. 이미 변경한 DB에서는 시드와 제안 수·내용이 다를 수 있다.
