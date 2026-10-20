package capstone;

import java.util.List;

import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * AI 작업 대기열 (가산점, 아키텍처 7.1). 대기열 이름과 RabbitMQ 설정만 있고, 넣고 꺼내는 일은 {@link Tasks}가 한다.
 *
 * <pre>
 * Tasks.start/decide ─▶ ai.tasks ─▶ Tasks.deliver ─HTTP─▶ agent
 *                          ▲             │ 실패
 *                          │ 30초 뒤     ├─ 1번째 → ai.tasks.retry.30s
 *                          │ 2분 뒤      ├─ 2번째 → ai.tasks.retry.2m
 *                          └─────────────┘ 3번째 → ai.tasks.dlq + FAILED
 * </pre>
 *
 * <p><b>꺼내는 쪽도 backend다</b> (decisions.md 6.24). agent가 대기열을 직접 읽게 하면 C 파트 코드가 바뀐다.
 * backend가 꺼내 지금처럼 HTTP로 넘겨도 "작업이 사라지지 않고, 실패하면 시간을 두고 다시 보낸다"는 효과는 같다.
 *
 * <p><b>기다리는 법</b>: 재시도 대기열은 소비자가 없고 메시지 수명(TTL)만 있다. 수명이 다하면 RabbitMQ가
 * 메시지를 {@code ai.tasks}로 되돌린다(dead-letter). 지연 플러그인 없이 기본 기능만으로 된다.
 */
@Configuration
class TaskQueue {

    static final String TASKS = "ai.tasks", DLQ = "ai.tasks.dlq";
    /** n번째 실패 뒤 들어갈 대기열. 개수 + 1 = 최대 시도 횟수 (아키텍처 7.1: 30초·2분 뒤, 3번) */
    static final List<String> RETRIES = List.of("ai.tasks.retry.30s", "ai.tasks.retry.2m");

    @Bean
    Declarables queues() {
        return new Declarables(QueueBuilder.durable(TASKS).build(),
                retry(RETRIES.get(0), 30_000), retry(RETRIES.get(1), 120_000),
                QueueBuilder.durable(DLQ).build());
    }

    private static Queue retry(String name, int waitMs) {
        return QueueBuilder.durable(name).ttl(waitMs).deadLetterExchange("").deadLetterRoutingKey(TASKS).build();
    }

    /** 메시지를 JSON으로. 기본(자바 직렬화)이면 사람이 대기열을 열어 봐도 못 읽는다 */
    @Bean
    MessageConverter messageConverter() {
        return new Jackson2JsonMessageConverter();
    }
}
