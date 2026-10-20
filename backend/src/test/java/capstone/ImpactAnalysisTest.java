package capstone;

import java.io.File;
import java.util.*;
import java.util.stream.Stream;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.testcontainers.utility.MountableFile;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 골든셋 테스트 — seed 데이터로 평가셋({@code eval/golden.json})의 정답이 나오는가.
 *
 * <p>아키텍처 11장 "기능 적합성" 증빙이다. compose와 같은 이미지에 db/*.sql을 그대로 돌려서
 * 시연 때와 같은 데이터로 계산한다.
 *
 * <p><b>정답은 이 파일이 아니라 eval/golden.json에 있다.</b> AI 파트의 Harness 평가도 같은 파일을 쓴다.
 * 정답이 한 곳에만 있어야 seed를 바꿨을 때 낡은 정답이 남지 않는다 (decisions.md 6.13).
 * seed를 바꿔 이 테스트가 깨지면, golden.json의 expected를 고치고 AI 파트에 알린다.
 *
 * <p>Docker가 있어야 돈다: {@code cd backend && mvn test}. 이미지 빌드(Dockerfile)는 테스트를 건너뛴다.
 */
class ImpactAnalysisTest {

    static final ObjectMapper MAPPER = new ObjectMapper();
    static PostgreSQLContainer<?> pg;
    static ImpactAnalysis analysis;
    static OptionAnalysis options;

    @BeforeAll
    static void startDb() {
        pg = new PostgreSQLContainer<>(DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"))
                .withDatabaseName("bom")
                .withCopyFileToContainer(MountableFile.forHostPath("../db"), "/docker-entrypoint-initdb.d");
        pg.start();
        // 슈퍼유저가 아니라 backend와 같은 app 계정으로 읽는다. 권한 문제도 같이 잡힌다
        JdbcTemplate db = new JdbcTemplate(new DriverManagerDataSource(pg.getJdbcUrl(), "app", "app"));
        analysis = new ImpactAnalysis(db);
        options = new OptionAnalysis(db, analysis);
    }

    // ------------------------------------------------------------------
    // 평가셋 eval/golden.json — 문항마다 테스트 하나
    // ------------------------------------------------------------------

    /**
     * 평가셋의 whatif를 넣어 계산하고, expected와 칸 단위로 비교한다.
     * expected에 적힌 칸만 비교한다 (rule_id·threshold 같은 나머지는 계약 2.2가 보장한다).
     * 순서까지 같아야 한다 — 응답은 날짜·품목 순으로 정렬돼 나온다.
     */
    @TestFactory
    Stream<DynamicTest> 평가셋_정답과_계산이_같다() throws Exception {
        JsonNode golden = new ObjectMapper().readTree(new File("../eval/golden.json"));
        assertThat(golden.get("policy_version").asText()).isEqualTo(ImpactAnalysis.POLICY_VERSION);
        List<DynamicTest> tests = new ArrayList<>();
        for (JsonNode c : golden.get("cases")) {
            tests.add(DynamicTest.dynamicTest(c.get("id").asText() + " " + c.get("title").asText(), () -> {
                List<Map<String, Object>> whatif = MAPPER.convertValue(c.get("whatif"), new TypeReference<>() {});
                Map<String, Object> actual = analysis.analyze(whatif);
                for (String key : List.of("shortages", "warnings", "excesses")) {
                    List<Map<String, Object>> expected = MAPPER.convertValue(c.get("expected").get(key), new TypeReference<>() {});
                    assertThat(pick(list(actual, key), expected))
                            .as("%s의 %s", c.get("id").asText(), key)
                            .isEqualTo(expected);
                }
                // compare_options 정답이 적힌 문항만 (verify 문항은 대안을 고른 뒤라 없다)
                if (c.get("expected").has("options")) {
                    Map<String, Object> actualOptions = options.compare(whatif, Set.of());
                    List<Map<String, Object>> expected = MAPPER.convertValue(c.get("expected").get("options"), new TypeReference<>() {});
                    assertThat(pick(list(actualOptions, "options"), expected))
                            .as("%s의 options", c.get("id").asText())
                            .isEqualTo(expected);
                    assertThat(actualOptions.get("recommended_option_id"))
                            .as("%s의 recommended_option_id", c.get("id").asText())
                            .isEqualTo(MAPPER.convertValue(c.get("expected").get("recommended_option_id"), Object.class));
                }
            }));
        }
        return tests.stream();
    }

    // ------------------------------------------------------------------
    // 오류 — retry_hint가 비어 있지 않은가
    // ------------------------------------------------------------------

    @Test
    void 없는_계획이면_NOT_FOUND와_비슷한_ID를_준다() {
        assertThatThrownBy(() -> run(op("reschedule_plan", "plan_id", "PP-5", "new_date", "2026-11-25")))
                .isInstanceOfSatisfying(ApiError.class, e -> {
                    assertThat(e.code).isEqualTo("NOT_FOUND");
                    assertThat(e.retryHint).contains("PP-01");
                });
    }

    @Test
    void 모르는_연산이면_INVALID_ARGUMENT와_연산_목록을_준다() {
        assertThatThrownBy(() -> run(op("move_plan", "plan_id", "PP-05")))
                .isInstanceOfSatisfying(ApiError.class, e -> {
                    assertThat(e.code).isEqualTo("INVALID_ARGUMENT");
                    assertThat(e.retryHint).contains("reschedule_plan");
                });
    }

    // ------------------------------------------------------------------
    // 읽기 쉽게 한 줄로 바꾸는 도우미
    // ------------------------------------------------------------------

    @SafeVarargs
    private static Map<String, Object> run(Map<String, Object>... ops) {
        return analysis.analyze(List.of(ops));
    }

    private static Map<String, Object> op(String name, Object... kv) {
        Map<String, Object> op = new LinkedHashMap<>(InternalApi.m(kv));
        op.put("op", name);
        return op;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> list(Map<String, Object> r, String key) {
        return (List<Map<String, Object>>) r.get(key);
    }

    /** 실제 결과에서 expected가 가진 칸만 남긴다. 숫자 타입을 JSON 쪽과 맞춘다. */
    private static List<Map<String, Object>> pick(List<Map<String, Object>> actual, List<Map<String, Object>> expected) {
        Set<String> keys = new HashSet<>();
        expected.forEach(e -> keys.addAll(e.keySet()));
        if (keys.isEmpty()) keys.addAll(List.of("item_id", "plan_id", "plan_date", "date", "projected_stock", "qty", "type", "args"));
        List<Map<String, Object>> picked = actual.stream().map(e -> {
            Map<String, Object> row = new LinkedHashMap<>();
            e.forEach((k, v) -> { if (keys.contains(k)) row.put(k, v); });
            return row;
        }).toList();
        try {   // JSON으로 한 번 왕복시켜 Long/Integer 차이를 없앤다 (golden.json에서 읽은 쪽과 같은 타입이 되게)
            return MAPPER.readValue(MAPPER.writeValueAsString(picked), new TypeReference<>() {});
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
