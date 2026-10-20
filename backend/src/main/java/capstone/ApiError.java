package capstone;

import java.util.Map;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import static capstone.InternalApi.m;

/**
 * 업무 오류. {@code /internal/**}에서 4xx + {@code {error, message, retry_hint}}로 나간다 (contracts.md 2.5).
 *
 * <p>retry_hint는 비워 두지 않는다 — 모델이 이걸 읽고 인자를 고쳐 스스로 다시 부른다.
 * 판정(결품·경고·과잉)은 오류가 아니다. 호출 자체가 성립하지 않을 때만 쓴다.
 */
class ApiError extends RuntimeException {
    final String code, retryHint;
    final HttpStatus status;

    ApiError(String code, String message, String retryHint) {
        super(message);
        this.code = code;
        this.retryHint = retryHint;
        // 없는 것은 404, 상태가 안 맞는 것은 409, 승인 없음은 403, 나머지는 요청이 틀린 것이라 400.
        // business-mcp는 상태가 아니라 error 코드로 분기한다. 상태를 나눈 건 로그에서 구분하려는 것
        this.status = switch (code) {
            case "NOT_FOUND" -> HttpStatus.NOT_FOUND;
            case "PO_NOT_OPEN", "PLAN_NOT_OPEN" -> HttpStatus.CONFLICT;
            case "APPROVAL_REQUIRED", "ROLE_NOT_ALLOWED" -> HttpStatus.FORBIDDEN;
            case "APPROVAL_NOT_PENDING" -> HttpStatus.CONFLICT;
            default -> HttpStatus.BAD_REQUEST;
        };
    }

    /** 인자가 없거나 형식이 틀렸다. 계약 2.5의 9개 코드 어디에도 안 맞아서 더한 코드다 (decisions.md 6.12). */
    static ApiError invalid(String message, String retryHint) {
        return new ApiError("INVALID_ARGUMENT", message, retryHint);
    }
}

/** {@link ApiError}를 응답으로 바꾼다. /internal과 /api가 같은 오류 형식을 쓴다. */
@RestControllerAdvice
class ApiErrors {

    @ExceptionHandler(ApiError.class)
    ResponseEntity<Map<String, Object>> onApiError(ApiError e) {
        return ResponseEntity.status(e.status).body(m("error", e.code, "message", e.getMessage(), "retry_hint", e.retryHint));
    }

    /** ?from=bad 처럼 쿼리 값 형식이 틀린 경우. Spring 기본 400 대신 같은 오류 형식으로 낸다. */
    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    ResponseEntity<Map<String, Object>> onBadParam(MethodArgumentTypeMismatchException e) {
        return onApiError(ApiError.invalid(e.getName() + " 값을 읽지 못했습니다: " + e.getValue(),
                "날짜는 YYYY-MM-DD 형식으로 주세요. 예: 2026-11-25"));
    }
}
