package capstone;

import java.util.List;
import java.util.Map;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.MethodParameter;
import org.springframework.http.MediaType;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyAdvice;

/**
 * masking 모듈 — 역할별 금액 가리기 (규칙 SEC-001, contracts.md 1.2, 기획안 2.4).
 *
 * <p><b>규칙은 여기 한 곳에만 있다.</b> 응답이 나가기 직전에 JSON 전체를 훑어 칸 이름으로 가린다.
 * API마다 가리면 새 API를 만들 때 빠뜨린다. 칸 이름으로 가리면 어떤 API든 같은 이름이면 같이 가려진다.
 *
 * <table>
 *   <tr><th>칸</th><th>PLANNER</th><th>BUYER</th><th>APPROVER</th></tr>
 *   <tr><td>unit_price (단가·계약 단가)</td><td>null</td><td>null</td><td>보임</td></tr>
 *   <tr><td>amount · added_cost · saved_amount (총액·추가 비용·덜 사는 금액)</td><td>null</td><td>보임</td><td>보임</td></tr>
 *   <tr><td>cost_level (추가 비용 등급)</td><td>보임</td><td>보임</td><td>보임</td></tr>
 * </table>
 *
 * <p><b>키는 남기고 값만 null로</b> 바꾼다. 키를 지우면 역할마다 응답 모양이 달라져 읽는 쪽이 분기해야 한다 (decisions.md 6.10).
 *
 * <p>역할은 {@code /api/**}에서는 JWT의 role, {@code /internal/**}에서는 {@code X-Role} 헤더에서 읽는다.
 * <b>역할을 모르면 가장 많이 가린다(PLANNER).</b> 헤더를 빠뜨렸을 때 금액이 새는 것보다 안 보이는 쪽이 안전하다.
 */
@RestControllerAdvice
class Masking implements ResponseBodyAdvice<Object> {

    private static final Logger log = LoggerFactory.getLogger(Masking.class);
    static final Set<String> ROLES = Set.of("PLANNER", "BUYER", "APPROVER");

    @Override
    public boolean supports(MethodParameter returnType, Class<? extends HttpMessageConverter<?>> converterType) {
        return true;
    }

    @Override
    public Object beforeBodyWrite(Object body, MethodParameter returnType, MediaType contentType,
                                  Class<? extends HttpMessageConverter<?>> converterType,
                                  ServerHttpRequest request, ServerHttpResponse response) {
        String path = request.getURI().getPath();
        if (!path.startsWith("/api/") && !path.startsWith("/internal/")) return body;
        String role = path.startsWith("/internal/")
                ? request.getHeaders().getFirst("X-Role")
                : SecurityContextHolder.getContext().getAuthentication() != null
                  && SecurityContextHolder.getContext().getAuthentication().getPrincipal() instanceof Jwt jwt
                  ? jwt.getClaimAsString("role") : null;
        mask(body, role);
        return body;
    }

    /** 응답 JSON(Map·List)을 제자리에서 고친다. 응답은 요청마다 새로 만든 객체라 고쳐도 다른 요청에 영향이 없다. */
    static void mask(Object node, String role) {
        String r = role == null ? null : role.toUpperCase();
        if (r == null || !ROLES.contains(r)) {   // Set.of는 contains(null)에서 예외를 던진다
            if (role != null) log.warn("모르는 역할 '{}' → PLANNER 기준으로 가립니다", role);
            r = "PLANNER";
        }
        walk(node, !r.equals("APPROVER"), r.equals("PLANNER"));
    }

    @SuppressWarnings("unchecked")
    private static void walk(Object node, boolean hideUnitPrice, boolean hideTotals) {
        if (node instanceof Map<?, ?> map) {
            Map<String, Object> mm = (Map<String, Object>) map;
            for (var e : mm.entrySet()) {
                String k = e.getKey();
                if (hideUnitPrice && k.equals("unit_price")) e.setValue(null);
                else if (hideTotals && (k.equals("amount") || k.equals("added_cost") || k.equals("saved_amount"))) e.setValue(null);
                else walk(e.getValue(), hideUnitPrice, hideTotals);
            }
        } else if (node instanceof List<?> list) {
            list.forEach(x -> walk(x, hideUnitPrice, hideTotals));
        }
    }
}
