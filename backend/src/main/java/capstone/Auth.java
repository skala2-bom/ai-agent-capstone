package capstone;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;

import javax.crypto.spec.SecretKeySpec;

import jakarta.servlet.DispatcherType;
import jakarta.servlet.http.HttpServletRequest;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.web.BearerTokenResolver;
import org.springframework.security.oauth2.server.resource.web.DefaultBearerTokenResolver;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.bind.annotation.*;

import static capstone.InternalApi.m;

/**
 * auth 모듈 — 로그인, JWT 발급·검증, 경로별 인증 (아키텍처 10장, contracts.md 3.1).
 *
 * <p>JWT를 고른 이유: 역할(role)을 토큰 안에 담아 두면 backend가 작업을 만들 때 그대로 agent에 넘길 수 있다
 * (contracts.md 1.1 요청자 헤더). 세션이면 서버가 상태를 들고 있어야 한다 (decisions.md 6.11).
 *
 * <p>토큰 서명은 HS256(공유 비밀키 하나)이다. 발급하는 쪽과 검증하는 쪽이 둘 다 backend라서
 * 공개키 방식(RS256)으로 나눌 이유가 없다.
 */
@Configuration
@RestController
class Auth {

    /** 시연 하루를 넘기면 다시 로그인한다. 갱신 토큰(refresh)은 만들지 않는다 */
    static final long TOKEN_HOURS = 8;

    private final SecretKeySpec key;
    private final JdbcTemplate db;

    // ponytail: 기본 비밀키가 코드에 있다. 내부망 시연용. 밖에 띄우면 .env의 JWT_SECRET을 반드시 바꾼다
    Auth(@Value("${JWT_SECRET:dev-only-secret-change-me-at-least-32-bytes}") String secret, JdbcTemplate db) {
        this.key = new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256");
        this.db = db;
    }

    /**
     * 경로별 규칙.
     * <ul>
     *   <li>{@code /api/auth/login} — 누구나</li>
     *   <li>{@code /api/**} — JWT 필수. 역할별 세부 권한은 각 API가 본다 (승인은 APPROVER 등)</li>
     *   <li>{@code /internal/**} — JWT를 안 쓴다. 사람이 아니라 서비스가 부르는 곳이라 서비스 토큰으로 막는다 (9단계).
     *       요청자 역할은 {@code X-Role} 헤더로 받는다 ({@link Masking})</li>
     * </ul>
     */
    @Bean
    SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        JwtAuthenticationConverter roles = new JwtAuthenticationConverter();
        roles.setJwtGrantedAuthoritiesConverter(jwt -> List.of(new SimpleGrantedAuthority("ROLE_" + jwt.getClaimAsString("role"))));
        return http
                // 쿠키를 안 쓰고 Authorization 헤더로만 인증하므로 CSRF 공격 경로가 없다
                .csrf(c -> c.disable())
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(a -> a
                        // SSE는 응답을 비동기로 이어 쓴다. 그 재진입(ASYNC)까지 다시 검사하면 이미 보낸 응답 위에서 거부가 나서 스트림이 깨진다.
                        // 첫 요청에서 이미 검사했으므로 재진입은 통과시킨다
                        .dispatcherTypeMatchers(DispatcherType.ASYNC, DispatcherType.ERROR).permitAll()
                        .requestMatchers("/api/auth/login").permitAll()
                        // ponytail: /internal은 아직 누구나 부를 수 있다. 서비스 토큰(X-Service-Token)은 9단계
                        .requestMatchers("/internal/**").permitAll()
                        .requestMatchers("/api/**").authenticated()
                        .anyRequest().denyAll())
                .oauth2ResourceServer(o -> o
                        .bearerTokenResolver(tokenResolver())
                        .jwt(j -> j.decoder(jwtDecoder()).jwtAuthenticationConverter(roles)))
                .build();
    }

    /**
     * 토큰은 보통 {@code Authorization: Bearer …} 헤더로 받는다.
     * <b>SSE 구독만 예외로 {@code ?access_token=}을 받는다.</b> 브라우저 EventSource는 헤더를 붙일 수 없다.
     * 주소에 토큰이 남으면 로그에 찍힐 수 있어서 이 한 경로에만 연다.
     */
    private static BearerTokenResolver tokenResolver() {
        DefaultBearerTokenResolver header = new DefaultBearerTokenResolver();
        DefaultBearerTokenResolver query = new DefaultBearerTokenResolver();
        query.setAllowUriQueryParameter(true);
        return (HttpServletRequest req) -> req.getRequestURI().endsWith("/stream") ? query.resolve(req) : header.resolve(req);
    }

    @Bean
    JwtDecoder jwtDecoder() {
        return NimbusJwtDecoder.withSecretKey(key).macAlgorithm(MacAlgorithm.HS256).build();
    }

    // ------------------------------------------------------------------
    // 역할 검사
    // ------------------------------------------------------------------

    /** 팀장은 구매 담당자 권한을 모두 가진다 (decisions.md 1.4). 생산관리와 구매는 서로 겹치지 않는다. */
    static boolean allows(String have, String need) {
        return need.equals(have) || ("BUYER".equals(need) && "APPROVER".equals(have));
    }

    /** API 안에서 역할을 본다. @PreAuthorize 대신 쓰는 이유: 거부할 때도 같은 오류 형식(retry_hint 포함)으로 내려고 */
    static void require(Jwt user, String need) {
        String have = user.getClaimAsString("role");
        if (!allows(have, need))
            throw new ApiError("ROLE_NOT_ALLOWED", need + " 권한이 필요합니다 (지금 " + have + ").",
                    "이 작업은 " + need + (need.equals("BUYER") ? " 또는 APPROVER" : "") + " 계정으로 해 주세요.");
    }

    // ------------------------------------------------------------------
    // 로그인
    // ------------------------------------------------------------------

    /**
     * POST /api/auth/login — {@code {user_id, password}} → JWT.
     *
     * <p>응답의 {@code role}·{@code name}은 화면이 버튼을 보이거나 숨기는 데만 쓴다. 권한 판단은 backend가 한다 (교재 5장).
     * 틀린 아이디와 틀린 비밀번호를 구분하지 않는다 — 구분하면 어떤 아이디가 있는지 알려 주는 셈이다.
     */
    @PostMapping("/api/auth/login")
    ResponseEntity<Map<String, Object>> login(@RequestBody Map<String, String> body) {
        List<Map<String, Object>> rows = db.queryForList(
                "select user_id, name, role, password_hash from app_user where user_id = ?", body.get("user_id"));
        String password = body.getOrDefault("password", "");
        if (rows.isEmpty() || !new BCryptPasswordEncoder().matches(password, (String) rows.getFirst().get("password_hash")))
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(m("error", "INVALID_CREDENTIALS",
                    "message", "아이디 또는 비밀번호가 틀렸습니다.", "retry_hint", "시연 계정: planner · buyer · approver"));
        Map<String, Object> user = rows.getFirst();
        Instant exp = Instant.now().plus(TOKEN_HOURS, ChronoUnit.HOURS);
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .subject((String) user.get("user_id"))
                .claim("role", user.get("role"))
                .claim("name", user.get("name"))
                .issuedAt(Instant.now()).expiresAt(exp).build();
        String token = new NimbusJwtEncoder(new com.nimbusds.jose.jwk.source.ImmutableSecret<>(key))
                .encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue();
        return ResponseEntity.ok(m("token", token, "user_id", user.get("user_id"), "name", user.get("name"),
                "role", user.get("role"), "expires_at", exp.toString()));
    }
}
