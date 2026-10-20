-- 업무 테이블 (public 스키마, app 계정 소유)
--
-- 실행 순서: init.sql → schema.sql → seed.sql (파일 이름 알파벳 순서). 빈 볼륨일 때만 돈다.
-- 고친 뒤 다시 돌리려면: docker compose down -v && docker compose up --build
--
-- 마이그레이션 도구(Flyway)를 쓰지 않는 이유: 시연용 데이터라 "지우고 처음부터"가 기본 동작이다.
-- 운영 데이터를 보존하며 구조를 바꿀 일이 생기면 그때 붙인다.
--
-- 테이블 구조는 B 파트 내부 결정이다 (contracts.md 4). 밖으로 나가는 모양은 /internal 응답이 정한다.

-- 슈퍼유저로 돌지만 app 계정으로 만들어야 app이 소유자가 된다 (권한을 따로 줄 필요가 없다)
SET ROLE app;

-- 로그인 사용자 (auth). 역할은 셋뿐이다 (contracts.md 1.1)
CREATE TABLE app_user (
    user_id       text PRIMARY KEY,
    name          text NOT NULL,
    role          text NOT NULL CHECK (role IN ('PLANNER', 'BUYER', 'APPROVER')),
    password_hash text NOT NULL            -- bcrypt. Spring BCryptPasswordEncoder가 그대로 읽는다
);

CREATE TABLE supplier (
    supplier_id text PRIMARY KEY,
    name        text NOT NULL,
    is_primary  boolean NOT NULL           -- false = 대체 협력사. 평소 발주는 안 하고 긴급 구매 대안으로만 나온다
);

-- 품목. 완제품·반제품·구매 부품을 한 테이블에 둔다 (BOM이 셋을 같은 방식으로 잇기 때문)
CREATE TABLE item (
    item_id        text PRIMARY KEY,
    name           text NOT NULL,
    -- ASSY = 반제품(메인보드·무선모듈). 재고·발주·생산계획이 없고 생산 당일 조립된다 (기획안 4.2, decisions.md 1.6).
    --        BOM 전개에서는 통과만 하고, 수량은 그 아래 구매 부품에 곱해 넘긴다 (F-02 "구매 부품 단위로 전개")
    type           text NOT NULL CHECK (type IN ('FG', 'ASSY', 'PART')),
    unit           text NOT NULL DEFAULT 'EA',
    lead_time_days int,                    -- PART만. 주 협력사 기준
    moq            int,                    -- PART만. 최소 발주 수량
    safety_stock   int NOT NULL DEFAULT 0, -- 담당자가 직접 입력 (decisions.md 1.1)
    high_value     boolean NOT NULL DEFAULT false,  -- 과잉(INV-003)은 이게 true인 부품만 본다. 기획안의 "구분" 칸
    supplier_id    text REFERENCES supplier,        -- 주 협력사. PART만
    CHECK (type <> 'PART' OR (lead_time_days IS NOT NULL AND moq IS NOT NULL AND supplier_id IS NOT NULL))
);

-- BOM. 부모 1개에 들어가는 자식 개수.
-- valid_from/valid_to: 설계변경(ECN) 적용일. 생산일이 이 기간 안일 때만 그 줄을 쓴다 (양끝 포함, null = 제한 없음).
-- 도어센서 케이스가 12/1에 바뀌는 SC3가 이걸로 계산된다.
CREATE TABLE bom (
    parent_id  text NOT NULL REFERENCES item,
    child_id   text NOT NULL REFERENCES item,
    qty_per    int  NOT NULL CHECK (qty_per > 0),
    valid_from date,
    valid_to   date,
    PRIMARY KEY (parent_id, child_id),
    CHECK (parent_id <> child_id)          -- 자기 자신만 막는다. 더 긴 순환은 전개할 때 BOM_CYCLE로 잡는다
);

-- 현재 재고. 창고 1곳 (기획안 4장 단순화 가정). FG·PART만 있다
CREATE TABLE inventory (
    item_id  text PRIMARY KEY REFERENCES item,
    on_hand  int NOT NULL CHECK (on_hand >= 0)
);

CREATE TABLE production_plan (
    plan_id    text PRIMARY KEY,
    product_id text NOT NULL REFERENCES item,
    plan_date  date NOT NULL,
    qty        int  NOT NULL CHECK (qty > 0),
    status     text NOT NULL DEFAULT 'PLANNED' CHECK (status IN ('PLANNED', 'DONE', 'CANCELLED'))
);

-- 발주. 금액(amount)은 저장하지 않고 qty × unit_price로 낸다 — 수량을 바꿀 때 같이 고칠 칸이 없어진다
CREATE TABLE purchase_order (
    po_id        text PRIMARY KEY,         -- PO-{발주 연월 YYMM}-{번호}. 새 번호는 backend가 만든다 (decisions.md 3.2)
    item_id      text NOT NULL REFERENCES item,
    supplier_id  text NOT NULL REFERENCES supplier,
    qty          int  NOT NULL CHECK (qty > 0),
    unit_price   int  NOT NULL,            -- 원. 발주 당시 계약 단가 (나중에 평가지표 단가가 바뀌어도 그대로)
    order_date   date NOT NULL,
    due_date     date NOT NULL,            -- 입고 예정일. 그날 입고분은 그날 쓸 수 있다 (아키텍처 1.2)
    status       text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RECEIVED', 'CANCELLED')),
    parent_po_id text REFERENCES purchase_order   -- split_po로 떼어낸 발주면 원 발주 ID
);
CREATE INDEX ON purchase_order (item_id, due_date) WHERE status = 'OPEN';

-- 협력사 × 부품 평가지표 (decisions.md 2.2). compare_options가 대안의 비용·위험을 여기서 계산한다.
-- decisions 2.2에서 칸 2개를 더했고, expedite_cost_per_day의 단위를 개·일로 바꿨다:
--   lead_time_days — 대체 협력사는 리드타임이 다르다. item.lead_time_days는 주 협력사 값이다
--   split_fee      — 분할 배송 수수료 (decisions.md 3.4). 계약 조건 문서에 있는 숫자를 계산이 쓰려면 DB에도 있어야 한다
CREATE TABLE supplier_metric (
    supplier_id           text NOT NULL REFERENCES supplier,
    item_id               text NOT NULL REFERENCES item,
    unit_price            int  NOT NULL,   -- 원
    lead_time_days        int  NOT NULL,
    -- 원/(개·일). 이미 낸 발주에서 1개를 1일 당길 때 붙는 비용. 많이·오래 당길수록 비싸다.
    -- 발주 1건 기준으로 두면 "전체 당기기"가 "급한 만큼만 나눠 당기기"보다 항상 싸져서 분할의 의미가 없어진다 (decisions.md 6.14)
    expedite_cost_per_day int  NOT NULL,
    split_fee             int  NOT NULL,   -- 원. 발주 1건을 나눠 받을 때 한 번
    delay_penalty         int  NOT NULL,   -- 원. 납기 지연 시 우리가 무는 패널티 (decisions.md 2.2 그대로)
    on_time_rate          numeric(5,2) NOT NULL CHECK (on_time_rate BETWEEN 0 AND 100),  -- %
    quality_score         int  NOT NULL CHECK (quality_score BETWEEN 0 AND 100),
    evaluated_at          date NOT NULL,
    evaluated_by          text NOT NULL REFERENCES app_user,
    PRIMARY KEY (supplier_id, item_id)
);

-- AI 작업 (task 모듈). 질문 하나 = 작업 하나. 재시작해도 남아야 승인 후 재개와 작업 이력이 된다
CREATE TABLE ai_task (
    task_id     text PRIMARY KEY,
    thread_id   text NOT NULL,                  -- agent checkpointer의 대화 열쇠. 지금은 task_id와 같다
    user_id     text NOT NULL REFERENCES app_user,
    role        text NOT NULL,                  -- 접수 당시 역할. agent에 넘긴 값 그대로 (금액 가리기 기준)
    question    text NOT NULL,
    trigger     text NOT NULL DEFAULT 'USER' CHECK (trigger IN ('USER', 'SYSTEM')),   -- SYSTEM = 입고 지연 자동 분석 (9단계)
    status      text NOT NULL DEFAULT 'QUEUED'
                CHECK (status IN ('QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'DONE', 'FAILED')),
    answer      text,                           -- DONE 이벤트의 answer
    result      jsonb,                          -- DONE 이벤트의 result
    sources     jsonb,                          -- DONE 이벤트의 sources (근거 문서)
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX ON ai_task (user_id, created_at DESC);

-- 진행 이벤트. SSE로 보낸 것과 똑같은 내용(taskId·seq·ts 포함)을 그대로 둔다.
-- 화면이 늦게 구독하거나 다시 연결하면 여기서 처음부터 다시 보낸다 (아키텍처 9.3)
CREATE TABLE task_event (
    task_id    text NOT NULL REFERENCES ai_task,
    seq        int  NOT NULL,                   -- backend가 매긴다. 작업 안에서 1부터
    type       text NOT NULL,
    payload    jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task_id, seq)
);

-- 승인 카드 (approval 모듈). 변경 도구는 금액과 무관하게 전부 여기를 거친다 (decisions.md 6.1).
-- 상태: PENDING → APPROVED | REJECTED, APPROVED → EXECUTED. 만료 없음 (decisions.md 1.7)
CREATE TABLE approval (
    approval_id    text PRIMARY KEY,           -- backend가 만든다. agent는 이벤트를 보낼 때 이 값을 모른다 (contracts.md 3.3)
    task_id        text NOT NULL REFERENCES ai_task,
    tool           text NOT NULL,              -- 변경 도구 이름. 실행할 때 이것과
    args           jsonb NOT NULL,             -- 이 인자가 그대로 와야만 실행한다 (contracts.md 2.7)
    summary        text,
    amount         bigint,                     -- 원. 화면에 보낼 때 역할대로 가린다
    evidence       jsonb NOT NULL DEFAULT '[]',
    before         jsonb,                      -- 변경 전후. 사람이 카드에서 한눈에 비교하게 (contracts.md 3.3)
    after          jsonb,
    status         text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED')),
    requested_by   text NOT NULL REFERENCES app_user,
    requested_at   timestamptz NOT NULL DEFAULT now(),
    decided_by     text REFERENCES app_user,
    decided_at     timestamptz,
    reason         text,                       -- 거절 사유 (필수) 또는 승인 메모
    executed_at    timestamptz,
    CHECK (status <> 'REJECTED' OR coalesce(reason, '') <> '')
);
CREATE INDEX ON approval (status, requested_at);

-- 변경 이력 (규칙 AUD-001). 발주·생산계획이 바뀔 때마다 한 줄. 변경과 같은 트랜잭션에서 쓴다
CREATE TABLE change_history (
    id          bigserial PRIMARY KEY,
    target_type text NOT NULL CHECK (target_type IN ('PURCHASE_ORDER', 'PRODUCTION_PLAN')),
    target_id   text NOT NULL,
    action      text NOT NULL,                 -- 변경 도구 이름, 또는 화면 변경이면 update_plan · supplier_delay
    before      jsonb,
    after       jsonb,
    reason      text,
    changed_by  text NOT NULL REFERENCES app_user,
    approval_id text REFERENCES approval,      -- 승인을 거친 변경이면 그 승인. 화면에서 직접 바꾼 사실 기록이면 null
    changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON change_history (target_type, target_id);

RESET ROLE;
