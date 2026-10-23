package capstone;

import java.time.Instant;
import java.util.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import static capstone.InternalApi.m;
import static capstone.TaskBatchExecution.*;

/** Shared department requests. The source chat remains private; only this explicit snapshot is shared. */
@RestController
class CoordinationRequests {
    final JdbcTemplate db;
    final CoordinationStore store;
    final ProductionPlanChanges plans;
    final ImpactAnalysis impact;
    final SupplierDelays delays;
    CoordinationRequests(JdbcTemplate db, ObjectMapper json, Approvals approvals, ImpactAnalysis impact) {
        this.db = db; store = new CoordinationStore(db, json);
        plans = new ProductionPlanChanges(db, approvals); this.impact = impact;
        delays = new SupplierDelays(db, approvals);
    }

    @GetMapping("/api/coordination/preview")
    Map<String, Object> preview(@RequestParam String taskId, @AuthenticationPrincipal Jwt user) {
        return store.events.tx.execute(tx -> { BusinessSnapshot.lock(db); return draft(taskId, user); });
    }

    Map<String, Object> draft(String taskId, Jwt user) {
        String role = user.getClaimAsString("role");
        if (!Set.of("PLANNER", "BUYER").contains(role)) throw denied("생산관리·구매 담당자가 부서 요청을 보낼 수 있습니다.");
        var rows = db.queryForList("select user_id,status,result::text as result from ai_task where task_id=?", taskId);
        if (rows.isEmpty() || !user.getSubject().equals(rows.getFirst().get("user_id"))) throw denied("본인의 분석 결과만 전송할 수 있습니다.");
        if (!"DONE".equals(rows.getFirst().get("status"))) throw conflict("분석 완료 후 요청을 보내세요.");
        var result = store.events.read(Objects.toString(rows.getFirst().get("result"), "{}"));
        if (!"IMPACT".equals(result.get("intent"))) throw conflict("생산계획 변경 또는 입고 지연의 영향 분석 결과가 필요합니다.");
        List<Map<String, Object>> ops = List.of(), facts = List.of(), changes = List.of();
        boolean confirmDelay = false;
        if (role.equals("PLANNER")) {
            ops = plans.normalize(result.get("whatif"));
            changes = plans.preview(ops);
        } else {
            var fact = store.events.latest(taskId, "SUPPLIER_DELAY_FACT");
            if (fact.isEmpty()) {
                facts = delays.pending(result, store);
                ops = maps(result.get("whatif"));
                confirmDelay = true;
            } else {
                facts = fact.containsKey("facts") ? maps(fact.get("facts")) : List.of(m("poId", fact.get("poId"), "before", fact.get("before"), "after", fact.get("after"), "reason", fact.get("reason")));
                for (var f : facts) if (!store.same(delays.current((String) f.get("poId")), f.get("after")))
                    throw conflict("입고 정보가 지연 통보 이후 바뀌었습니다. 최신 정보로 다시 분석하세요.");
            }
        }
        var analysis = impact.analyze(ops);
        if (store.scope(analysis).isEmpty()) throw conflict("현재 계산에서 조정이 필요한 긴급 결품·과잉이 없습니다. 최신 분석 결과를 확인하세요.");
        return m("sourceTaskId", taskId, "direction", role.equals("PLANNER") ? "PLAN_TO_BUY" : "BUY_TO_PLAN",
                "planOps", role.equals("PLANNER") ? ops : List.of(), "planChanges", changes, "facts", facts, "analysis", analysis,
                "requiresDelayConfirmation", confirmDelay,
                "fingerprint", BusinessSnapshot.fingerprint(db, store.events));
    }

    @PostMapping("/api/coordination/requests")
    Map<String, Object> create(@RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        String id = text(body, "sourceTaskId"), command = text(body, "commandId"), message = text(body, "message");
        return store.events.tx.execute(tx -> {
            String parent = Objects.toString(body.get("parentRequestId"), "");
            if (parent.equals(id)) throw conflict("자기 자신을 이전 요청으로 연결할 수 없습니다.");
            if (!parent.isBlank()) store.lock(parent);
            store.events.lock(id);
            var existing = store.events.latest(id, CoordinationStore.STATE);
            if (!existing.isEmpty()) {
                if (!user.getSubject().equals(existing.get("requesterId"))) throw denied("본인의 분석만 전송할 수 있습니다.");
                if (command.equals(existing.get("creationCommandId")) && store.same(body, existing.get("creationCommand"))) return store.view(existing, user);
                throw conflict("이 분석으로 보낸 요청이 있습니다. 요청함에서 확인하세요. 조건을 바꾸려면 새로 분석하세요.");
            }
            BusinessSnapshot.lock(db);
            var draft = draft(id, user);
            if (!Objects.equals(draft.get("fingerprint"), body.get("expectedFingerprint"))) throw conflict("전송 전 업무 데이터가 바뀌었습니다. 요청 내용을 다시 확인하세요.");
            Map<String, Object> parentRequest = parent.isBlank() ? Map.of() : store.get(parent);
            if (!parentRequest.isEmpty()) {
                CoordinationStore.visible(parentRequest, user);
                if (!CoordinationStore.TERMINAL.contains(parentRequest.get("status"))) {
                    CoordinationStore.assigned(parentRequest, user);
                    if (!maps(parentRequest.get("appliedPlanChanges")).isEmpty())
                        throw conflict("생산계획은 이미 저장했습니다. 부서 요청함의 '남은 문제를 구매팀에 요청'으로 현재 문제만 전송하세요.");
                    if (!"BUY_TO_PLAN".equals(parentRequest.get("direction")) || !"PLAN_TO_BUY".equals(draft.get("direction"))
                            || "WAITING_LINKED".equals(parentRequest.get("status"))) throw conflict("연결된 요청의 검토가 끝난 뒤 새 요청을 보내세요.");
                } else if (!user.getSubject().equals(parentRequest.get("requesterId"))) throw denied("종료된 요청은 원래 요청자가 새 조건으로 다시 보낼 수 있습니다.");
            }
            if (Boolean.TRUE.equals(draft.get("requiresDelayConfirmation"))) {
                if (!Boolean.TRUE.equals(body.get("confirmDelay"))) throw conflict("입고 지연 사실의 저장·전송에 동의해 주세요.");
                String reason = text(body, "delayReason");
                List<Map<String, Object>> recorded = new ArrayList<>();
                for (var f : maps(draft.get("facts"))) {
                    var saved = delays.record((String) f.get("poId"), java.time.LocalDate.parse((String) map(f.get("after")).get("due_date")), reason, user.getSubject());
                    saved.put("itemName", f.get("itemName")); recorded.add(saved);
                }
                store.events.append(id, m("type", "SUPPLIER_DELAY_FACT", "facts", recorded, "recordedBy", user.getSubject()));
                draft.put("facts", recorded); draft.put("requiresDelayConfirmation", false);
            }
            var r = new LinkedHashMap<>(draft);
            r.putAll(m("id", id, "requesterId", user.getSubject(), "requesterName", store.name(user.getSubject()),
                    "recipientRole", "PLAN_TO_BUY".equals(draft.get("direction")) ? "BUYER" : "PLANNER",
                    "message", message, "status", "OPEN", "revision", 0, "managerVisible", false,
                    "createdAt", Instant.now().toString(), "creationCommandId", command, "creationCommand", body,
                    "parentRequestId", parent.isBlank() ? null : parent,
                    "planConsent", "PLAN_TO_BUY".equals(draft.get("direction")) ? m("by", user.getSubject(), "at", Instant.now().toString(), "ops", draft.get("planOps")) : null));
            r = new LinkedHashMap<>(store.save(r, Map.of()));
            if (!parentRequest.isEmpty() && !CoordinationStore.TERMINAL.contains(parentRequest.get("status")))
                store.save(parentRequest, m("status", "WAITING_LINKED", "linkedRequestId", id, "linkedOutcome", null));
            readVersion(r, user, number(r.get("revision")));
            return store.view(r, user);
        });
    }

    @GetMapping("/api/coordination/requests")
    List<Map<String, Object>> list(@AuthenticationPrincipal Jwt user) {
        // A small MVP projection over existing JSON events. No private chat/result joins.
        var rows = db.queryForList("select payload::text from (select distinct on(task_id) payload from task_event where type=? order by task_id,seq desc) latest order by payload->>'updatedAt' desc", String.class, CoordinationStore.STATE);
        List<Map<String, Object>> result = new ArrayList<>();
        for (String row : rows) {
            var r = store.events.read(row);
            try { CoordinationStore.visible(r, user); } catch (ApiError e) { continue; }
            result.add(store.view(r, user));
        }
        return result;
    }
    @GetMapping("/api/coordination/requests/{id}")
    Map<String, Object> get(@PathVariable String id, @AuthenticationPrincipal Jwt user) { return store.view(store.get(id), user); }

    @PostMapping("/api/coordination/requests/{id}/claim")
    Map<String, Object> claim(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        return store.events.tx.execute(tx -> {
            store.lock(id); var r = store.get(id); CoordinationStore.visible(r, user); CoordinationStore.active(r);
            if (!user.getClaimAsString("role").equals(r.get("recipientRole"))) throw denied("받는 부서의 담당자만 접수할 수 있습니다.");
            if (user.getSubject().equals(r.get("assigneeId"))) return store.view(r, user);
            CoordinationStore.revision(r, body.get("expectedRevision"));
            if (r.get("assigneeId") != null) throw conflict("다른 담당자가 먼저 접수했습니다.");
            return store.view(store.save(r, m("assigneeId", user.getSubject(), "assigneeName", store.name(user.getSubject()), "status", "IN_PROGRESS")), user);
        });
    }

    @PostMapping("/api/coordination/requests/{id}/read")
    Map<String, Object> read(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        return store.events.tx.execute(tx -> {
            store.lock(id); var r = store.get(id); CoordinationStore.visible(r, user);
            long revision = number(body.get("revision"));
            if (revision < 0 || revision > number(r.get("revision"))) throw conflict("읽음 버전이 올바르지 않습니다.");
            readVersion(r, user, revision); return m("read", true);
        });
    }
    void readVersion(Map<String, Object> r, Jwt user, long revision) {
        var rows = db.queryForList("select payload::text from task_event where task_id=? and type='COORDINATION_READ' and payload->>'userId'=? order by seq desc limit 1", String.class, r.get("id"), user.getSubject());
        if (!rows.isEmpty() && number(store.events.read(rows.getFirst()).get("revision")) >= revision) return;
        store.events.append((String) r.get("id"), m("type", "COORDINATION_READ", "userId", user.getSubject(), "revision", revision));
    }

    @PostMapping("/api/coordination/requests/{id}/escalate")
    Map<String, Object> escalate(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        return store.events.tx.execute(tx -> {
            store.lock(id); var r = store.get(id); CoordinationStore.assigned(r, user);
            if (!"PLAN_TO_BUY".equals(r.get("direction"))) throw denied("구매 담당자가 팀장에게 검토를 요청할 수 있습니다.");
            if (Boolean.TRUE.equals(r.get("managerVisible"))) return store.view(r, user);
            CoordinationStore.revision(r, body.get("expectedRevision"));
            return store.view(store.save(r, m("managerVisible", true, "status", "WAITING_REVIEW")), user);
        });
    }

    @PostMapping("/api/coordination/requests/{id}/reject")
    Map<String, Object> reject(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        String command = text(body, "commandId"), reason = text(body, "reason");
        return store.events.tx.execute(tx -> {
            store.lock(id); var r = store.get(id); CoordinationStore.visible(r, user);
            boolean allowed = "PLAN_TO_BUY".equals(r.get("direction"))
                    ? "APPROVER".equals(user.getClaimAsString("role")) && Boolean.TRUE.equals(r.get("managerVisible"))
                    : "PLANNER".equals(user.getClaimAsString("role")) && user.getSubject().equals(r.get("assigneeId"));
            if (!allowed) throw denied("이 요청의 최종 결정 권한이 없습니다.");
            var replay = store.events.request(id, "COORDINATION_DECISION", command);
            if (!replay.isEmpty()) {
                if (!"REJECT".equals(replay.get("action")) || !store.same(replay.get("body"), body) || !user.getSubject().equals(replay.get("actor"))) throw conflict("같은 요청 ID의 내용이 다릅니다.");
                return store.view(r, user);
            }
            CoordinationStore.active(r); CoordinationStore.revision(r, body.get("expectedRevision"));
            if ("WAITING_LINKED".equals(r.get("status"))) throw conflict("연결된 구매 요청을 먼저 처리하세요.");
            String task = (String) r.get("solutionTaskId");
            if (task != null) {
                store.events.lock(task);
                if (!store.events.latest(task, "TASK_BATCH_RESULT").isEmpty()) throw conflict("이미 실행 또는 취소된 작업입니다.");
                db.update("update approval set status='WITHDRAWN',reason=?,decided_by=?,decided_at=now() where task_id=? and status in ('DRAFT','PENDING')", reason, user.getSubject(), task);
                store.events.append(task, m("type", "TASK_BATCH_RESULT", "requestId", command, "status", "REJECTED", "executions", List.of(), "reason", reason));
                String answer = "부서 요청이 최종 거절되었습니다. 사유: " + reason;
                var result = m("intent", "ADJUST", "whatif", r.get("planOps"), "impact", r.get("analysis"),
                        "coordinationId", id, "executions", List.of(), "requestStatus", "REJECTED");
                db.update("update ai_task set status='DONE',answer=?,result=?::jsonb,finished_at=now() where task_id=?", answer, store.events.write(result), task);
                store.events.append(task, m("type", "DONE", "status", "DONE", "answer", answer, "result", result, "sources", List.of()));
            }
            store.events.append(id, m("type", "COORDINATION_DECISION", "action", "REJECT", "requestId", command, "body", body, "actor", user.getSubject()));
            return store.view(store.finish(r, "REJECTED", reason, user.getSubject(), m("changed", false)), user);
        });
    }

    @GetMapping("/internal/tasks/{id}/coordination")
    Map<String, Object> context(@PathVariable String id) {
        var r = store.linked(id);
        return r.isEmpty() ? m("linked", false) : m("linked", true, "status", r.get("status"), "reason", r.get("decisionReason"),
                "result", store.events.latest(id, "TASK_BATCH_RESULT"));
    }
    static String text(Map<String, Object> body, String key) {
        if (!(body.get(key) instanceof String v) || v.isBlank() || v.length() > (key.equals("commandId") ? 100 : 4000))
            throw conflict(key + " 값을 입력하세요. (최대 " + (key.equals("commandId") ? 100 : 4000) + "자)");
        return v.trim();
    }
    static ApiError denied(String text) { return new ApiError("ROLE_NOT_ALLOWED", text, "권한과 요청 담당자를 확인하세요."); }
}
