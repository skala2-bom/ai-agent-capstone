package capstone;

import java.time.LocalDate;
import java.util.Map;

import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.*;

/**
 * 화면이 부르는 조회 API (contracts.md 3.1). 로그인 필수, 금액은 {@link Masking}이 역할대로 가린다.
 *
 * <p>{@code /internal}과 <b>같은 메서드를 그대로 부른다.</b> 화면 조회와 AI 도구가 같은 숫자를 보게 하려는 것이다
 * (아키텍처 1.1 "규칙은 한 곳에"). 응답 모양도 {@code /internal}과 같다 (contracts.md 2.1 응답 예시).
 *
 * <p>{@code GET /api/items/{id}/bom}은 BOM 펼치기(explode_bom, 2차 도구)와 같이 만든다.
 */
@RestController
@RequestMapping("/api")
class ScreenApi {

    private final InternalApi internal;

    ScreenApi(InternalApi internal) {
        this.internal = internal;
    }

    @GetMapping("/plans")
    Map<String, Object> plans(@RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
                              @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to) {
        return internal.listPlans(from, to);
    }

    @GetMapping("/inventory")
    Map<String, Object> inventory(@RequestParam(required = false) String type,
                                  @RequestParam(name = "item_id", required = false) String itemId) {
        return internal.getInventory(type, itemId);
    }

    @GetMapping("/purchase-orders")
    Map<String, Object> purchaseOrders(@RequestParam(defaultValue = "OPEN") String status,
                                       @RequestParam(name = "item_id", required = false) String itemId,
                                       @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
                                       @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to) {
        return internal.listReceipts(status, itemId, from, to);
    }
}
