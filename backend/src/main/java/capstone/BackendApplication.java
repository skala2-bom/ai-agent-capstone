package capstone;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * backend — Application 계층 (아키텍처 3장). 시작점만 여기 있고, 일은 모듈별 파일이 한다 (아키텍처 3.1).
 *
 * <table>
 *   <tr><th>모듈</th><th>파일</th></tr>
 *   <tr><td>auth</td><td>{@link Auth} — 로그인, JWT, 역할 검사</td></tr>
 *   <tr><td>task</td><td>{@link Tasks} — AI 작업 접수, 진행 이벤트 저장·SSE 중계, 승인 결정 후 재개 / {@link TaskQueue} — 작업 대기열(RabbitMQ)</td></tr>
 *   <tr><td>approval</td><td>{@link Approvals} — 승인 카드, 실행 전 승인 확인, 변경 이력</td></tr>
 *   <tr><td>analysis</td><td>{@link ImpactAnalysis} · {@link OptionAnalysis} — 결품·과잉 판정, 해결 대안 비교</td></tr>
 *   <tr><td>master·plan·purchase·inventory</td><td>{@link InternalApi}(AI 도구용) · {@link ScreenApi}(화면용)</td></tr>
 *   <tr><td>masking</td><td>{@link Masking} — 역할별 금액 가리기</td></tr>
 * </table>
 *
 * <p><b>금지</b>: LLM을 직접 부르지 않는다 (교재 5장 금지 사항). AI 호출은 전부 agent가 한다.
 * 그래서 이 프로젝트의 backend 의존성에는 LLM 라이브러리가 아예 없다.
 *
 * <p>설계 배경: docs/walking-skeleton.md · 결정 근거: docs/decisions.md
 */
@SpringBootApplication
public class BackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(BackendApplication.class, args);
    }
}
