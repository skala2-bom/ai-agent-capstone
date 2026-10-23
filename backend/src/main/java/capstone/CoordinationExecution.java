package capstone;

import java.util.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import static capstone.InternalApi.m;
import static capstone.TaskBatchExecution.*;

/** The PLANNER explicitly confirms one preview; a BUYER never acquires plan-write authority. */
@RestController
class CoordinationExecution {
    final CoordinationStore store;
    final ProductionPlanChanges plans;
    final ImpactAnalysis impact;
    final JdbcTemplate db;
    CoordinationExecution(JdbcTemplate db, ObjectMapper json, Approvals approvals, ImpactAnalysis impact) {
        this.db = db; store = new CoordinationStore(db, json); plans = new ProductionPlanChanges(db, approvals); this.impact = impact;
    }
    @PostMapping("/api/coordination/requests/{id}/plan-preview")
    Map<String, Object> preview(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        return store.events.tx.execute(tx -> {
            store.lock(id); var r = store.get(id); permitted(r, user); CoordinationStore.revision(r, body.get("expectedRevision"));
            BusinessSnapshot.lock(db);
            var ops = plans.normalize(body.get("whatif")); var changes = plans.preview(ops);
            var before = impact.analyze(List.of()); var after = impact.analyze(ops);
            String error = null;
            var remaining = store.remaining(after, store.scope(map(r.get("analysis"))));
            boolean partial = !store.scope(remaining).isEmpty();
            try { validateChange(before, after, r, partial); }
            catch (ApiError e) { error = e.getMessage(); }
            String previewId = UUID.randomUUID().toString();
            store.events.append(id, m("type", "COORDINATION_PLAN_PREVIEW", "requestId", previewId, "actor", user.getSubject(),
                    "revision", r.get("revision"), "ops", ops, "changes", changes, "before", before,
                    "fingerprint", BusinessSnapshot.fingerprint(db, store.events), "canExecute", error == null));
            return m("previewId", previewId, "changes", changes, "impact", after, "remainingImpact", remaining,
                    "partial", partial, "canExecute", error == null, "error", error);
        });
    }
    @PostMapping("/api/coordination/requests/{id}/plan-execute")
    Map<String, Object> execute(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        String command = CoordinationRequests.text(body, "commandId"), reason = CoordinationRequests.text(body, "reason");
        return store.events.tx.execute(tx -> {
            store.lock(id); var r = store.get(id); CoordinationStore.visible(r, user);
            if (!"PLANNER".equals(user.getClaimAsString("role")) || !user.getSubject().equals(r.get("assigneeId")))
                throw CoordinationRequests.denied("담당 생산관리자만 계획 변경을 확정할 수 있습니다.");
            var replay = store.events.request(id, "COORDINATION_DECISION", command);
            if (!replay.isEmpty()) {
                if (!"PLAN_EXECUTE".equals(replay.get("action")) || !store.same(replay.get("body"), body) || !user.getSubject().equals(replay.get("actor"))) throw conflict("같은 요청 ID의 내용이 다릅니다.");
                return store.view(r, user);
            }
            permitted(r, user); CoordinationStore.revision(r, body.get("expectedRevision"));
            var preview = store.events.request(id, "COORDINATION_PLAN_PREVIEW", Objects.toString(body.get("previewId"), ""));
            if (preview.isEmpty() || !user.getSubject().equals(preview.get("actor")) || !Boolean.TRUE.equals(preview.get("canExecute"))
                    || number(preview.get("revision")) != number(r.get("revision"))) throw conflict("변경안을 먼저 검증하고 확인하세요.");
            BusinessSnapshot.lock(db);
            if (!Objects.equals(preview.get("fingerprint"), BusinessSnapshot.fingerprint(db, store.events)))
                throw conflict("검토 후 업무 데이터가 바뀌었습니다. 변경안을 다시 검증하세요.");
            var ops = plans.normalize(preview.get("ops")); var before = impact.analyze(List.of());
            var scope = store.scope(map(r.get("analysis")));
            var predicted = impact.analyze(ops);
            boolean partial = !store.scope(store.remaining(predicted, scope)).isEmpty();
            validateChange(before, predicted, r, partial);
            var changes = plans.apply(ops, "부서 요청 " + id + " / " + reason, user.getSubject());
            var after = impact.analyze(List.of()); validateChange(before, after, r, partial);
            var applied = new LinkedHashMap<String, Map<String, Object>>();
            for (var c : maps(r.get("appliedPlanChanges"))) applied.put((String) c.get("planId"), c);
            for (var c : changes) {
                var old = applied.get((String) c.get("planId"));
                applied.put((String) c.get("planId"), old == null ? c : m("planId", c.get("planId"), "before", old.get("before"), "after", c.get("after")));
            }
            store.events.append(id, m("type", "COORDINATION_DECISION", "action", "PLAN_EXECUTE", "requestId", command, "body", body, "actor", user.getSubject()));
            r = store.save(r, m("appliedPlanChanges", new ArrayList<>(applied.values()), "remainingAnalysis", store.remaining(after, scope)));
            if (partial) return store.view(store.save(r, m("status", "PARTIALLY_COMPLETED", "decisionReason", reason,
                    "outcome", m("planChanges", new ArrayList<>(applied.values()), "impact", after), "linkedOutcome", null)), user);
            return store.view(store.finish(r, "COMPLETED", reason, user.getSubject(), m("planChanges", new ArrayList<>(applied.values()), "impact", after)), user);
        });
    }
    void validateChange(Map<String, Object> before, Map<String, Object> after, Map<String, Object> r, boolean partial) {
        requireNoWorse(before, after);
        var scope = store.scope(map(r.get("analysis")));
        if (partial && !store.improves(before, after, scope))
            throw conflict("요청의 결품·과잉을 줄이지 못한 변경입니다. 다른 생산 변경안을 검토하세요.");
    }
    static void permitted(Map<String, Object> r, Jwt user) {
        CoordinationStore.assigned(r, user);
        if ("WAITING_LINKED".equals(r.get("status"))) throw conflict("연결된 구매 요청을 검토 중입니다. 그 요청에서 함께 실행하거나 최종 결과를 확인하세요.");
        if (!"BUY_TO_PLAN".equals(r.get("direction")) || !"PLANNER".equals(user.getClaimAsString("role")))
            throw CoordinationRequests.denied("생산관리 담당자가 받은 일정 조정 요청만 확정할 수 있습니다.");
    }
}
