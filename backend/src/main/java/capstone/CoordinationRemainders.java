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

/** A saved production change and its remaining purchasing work have separate lifecycles. */
@RestController
class CoordinationRemainders {
    final CoordinationStore store;
    final JdbcTemplate db;
    final ImpactAnalysis impact;
    CoordinationRemainders(JdbcTemplate db, ObjectMapper json, ImpactAnalysis impact) {
        this.db = db; this.impact = impact; store = new CoordinationStore(db, json);
    }
    void permitted(Map<String, Object> r, Jwt user) {
        CoordinationStore.assigned(r, user);
        if (!"PLANNER".equals(user.getClaimAsString("role")) || !"BUY_TO_PLAN".equals(r.get("direction"))
                || !"PARTIALLY_COMPLETED".equals(r.get("status")))
            throw CoordinationRequests.denied("생산계획을 먼저 저장한 담당 생산관리자가 남은 문제를 전달할 수 있습니다.");
    }
    Map<String, Object> current(Map<String, Object> r) {
        store.requireSavedPlans(r);
        var analysis = store.remaining(impact.analyze(List.of()), store.scope(map(r.get("analysis"))));
        if (store.scope(analysis).isEmpty()) throw conflict("현재 데이터에서 요청의 남은 결품·과잉이 없습니다. 생산 요청을 다시 검토하세요.");
        return m("analysis", analysis, "fingerprint", BusinessSnapshot.fingerprint(db, store.events), "revision", r.get("revision"));
    }
    @GetMapping("/api/coordination/requests/{id}/remaining-preview")
    Map<String, Object> preview(@PathVariable String id, @AuthenticationPrincipal Jwt user) {
        return store.events.tx.execute(tx -> {
            store.lock(id); var r = store.get(id); permitted(r, user); BusinessSnapshot.lock(db);
            return current(r);
        });
    }
    @PostMapping("/api/coordination/requests/{id}/remaining-request")
    Map<String, Object> send(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        String command = CoordinationRequests.text(body, "commandId"), message = CoordinationRequests.text(body, "message");
        return store.events.tx.execute(tx -> {
            store.lock(id); var parent = store.get(id); CoordinationStore.visible(parent, user);
            if (!"PLANNER".equals(user.getClaimAsString("role")) || !user.getSubject().equals(parent.get("assigneeId")))
                throw CoordinationRequests.denied("담당 생산관리자만 남은 문제를 전송할 수 있습니다.");
            var replay = store.events.request(id, "COORDINATION_REMAINING_SENT", command);
            if (!replay.isEmpty()) {
                if (!store.same(body, replay.get("body")) || !user.getSubject().equals(replay.get("actor"))) throw conflict("같은 전송 ID의 내용이 다릅니다.");
                return store.view(store.get((String) replay.get("childId")), user);
            }
            permitted(parent, user); CoordinationStore.revision(parent, body.get("expectedRevision")); BusinessSnapshot.lock(db);
            var snapshot = current(parent);
            if (!Objects.equals(snapshot.get("fingerprint"), body.get("expectedFingerprint")))
                throw conflict("검토 후 업무 데이터가 변경됐습니다. 남은 문제를 다시 확인하세요.");
            String child = UUID.randomUUID().toString();
            var result = m("intent", "IMPACT", "whatif", List.of(), "impact", snapshot.get("analysis"));
            // This is a calculator snapshot for an explicit request, not an invented AI response or model call.
            db.update("insert into ai_task(task_id,thread_id,user_id,role,question,trigger,status,result,answer,finished_at) values(?,?,?,'PLANNER',?,'SYSTEM','DONE',?::jsonb,?,now())",
                    child, child, user.getSubject(), "생산계획 저장 후 남은 구매 문제 전달", store.events.write(result), "현재 저장된 업무 데이터로 남은 결품·과잉을 다시 계산했습니다.");
            store.events.append(child, m("type", "DONE", "result", result, "answer", "현재 저장된 업무 데이터로 남은 결품·과잉을 다시 계산했습니다."));
            var r = m("id", child, "sourceTaskId", child, "direction", "PLAN_TO_BUY", "requestKind", "REMAINING",
                    "status", "OPEN", "revision", 0, "requesterId", user.getSubject(), "requesterName", store.name(user.getSubject()),
                    "recipientRole", "BUYER", "message", message, "planOps", List.of(), "planChanges", List.of(), "facts", List.of(),
                    "analysis", snapshot.get("analysis"), "appliedPlanChanges", parent.get("appliedPlanChanges"),
                    "planConsent", m("by", user.getSubject(), "at", Instant.now().toString(), "ops", List.of()),
                    "parentRequestId", id, "managerVisible", false, "createdAt", Instant.now().toString());
            var saved = store.save(r, Map.of());
            store.save(parent, m("status", "WAITING_LINKED", "linkedRequestId", child, "linkedOutcome", null, "remainingAnalysis", snapshot.get("analysis")));
            store.events.append(id, m("type", "COORDINATION_REMAINING_SENT", "requestId", command, "body", body, "actor", user.getSubject(), "childId", child));
            return store.view(saved, user);
        });
    }
}
