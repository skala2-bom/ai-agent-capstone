package capstone;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * {@code /internal/**} 서비스 토큰 검사 (아키텍처 10장, contracts.md 1.1 {@code X-Service-Token}).
 *
 * <p>{@code /internal}은 사람이 아니라 서비스가 부르는 곳이다. backend의 8080은 화면 때문에 공개 포트라,
 * 토큰이 없으면 밖에서도 발주를 바꾸는 API를 부를 수 있었다 (승인 확인은 있지만, 조회·이벤트 위조는 막히지 않았다).
 *
 * <p><b>서비스마다 토큰이 다르고, 각자 자기 경로만 열린다</b> (최소 권한):
 * <ul>
 *   <li>agent 토큰 → {@code /internal/tasks/**} (진행 이벤트 콜백)만</li>
 *   <li>business-mcp 토큰 → 나머지 {@code /internal/**} (업무 도구)만</li>
 * </ul>
 * 하나가 새도 다른 쪽 일은 못 한다. agent가 업무 데이터를 보려면 business-mcp 도구를 거쳐야 한다 (교재 5장 경로).
 *
 * <p>토큰 값은 compose 환경변수다 (기본값은 내부망 시연용, 밖에 띄우면 .env에서 바꾼다).
 */
@Component
class ServiceTokens extends OncePerRequestFilter {

    private final byte[] agentToken, businessMcpToken;

    ServiceTokens(@Value("${AGENT_SERVICE_TOKEN:dev-agent-token}") String agentToken,
                  @Value("${BUSINESS_MCP_SERVICE_TOKEN:dev-business-mcp-token}") String businessMcpToken) {
        this.agentToken = agentToken.getBytes(StandardCharsets.UTF_8);
        this.businessMcpToken = businessMcpToken.getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest req) {
        return !req.getRequestURI().startsWith("/internal/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        boolean events = req.getRequestURI().startsWith("/internal/tasks/");
        byte[] expected = events ? agentToken : businessMcpToken;
        String given = req.getHeader("X-Service-Token");
        // 길이·내용 비교를 한 번에, 시간이 값에 따라 달라지지 않게 (토큰을 한 글자씩 맞춰 보는 공격 방지)
        if (given != null && MessageDigest.isEqual(given.getBytes(StandardCharsets.UTF_8), expected)) {
            chain.doFilter(req, res);
            return;
        }
        res.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        res.setContentType("application/json;charset=UTF-8");
        res.getWriter().write("{\"error\":\"SERVICE_TOKEN_REQUIRED\",\"message\":\"서비스 토큰이 없거나 맞지 않습니다.\","
                + "\"retry_hint\":\"" + (events ? "agent" : "business-mcp")
                + "의 SERVICE_TOKEN 환경변수가 backend 설정과 같은지 확인하세요.\"}");
    }
}
