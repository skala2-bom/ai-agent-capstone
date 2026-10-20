-- 시연 데이터: 가상 회사 "가온스마트" (기획안 A1-A3, docs/BOM_Agent_기획안_A1-A3)
--
-- 기준일(오늘) 2026-11-02. 원칙은 **기획안 숫자를 그대로** 옮기는 것이다.
-- 기획안과 다른 곳은 전부 decisions.md 7절 표에 이유와 함께 있다. 여기서는 줄 끝에 [변경]으로 표시한다.
--
-- 이 데이터에 SC1~SC4 골든셋 정답이 달려 있다 (decisions.md 7.3).
-- 재고·발주·생산계획 숫자를 바꾸면 정답을 다시 계산해야 한다.
--
-- 변경 전 상태에서는 결품·경고·과잉이 0건이다 (기획안 4.4). 일정을 바꾸는 순간 생기는 문제는 모두 그 변경 때문이다.

-- 시연 계정 비밀번호 해시용. seed가 슈퍼유저로 돌 때만 쓴다
CREATE EXTENSION IF NOT EXISTS pgcrypto;

SET ROLE app;

-- ------------------------------------------------------------------
-- 사용자 3명 (기획안 2.1). 시연 비밀번호는 모두 1234
-- ------------------------------------------------------------------
INSERT INTO app_user VALUES
  ('planner',  '김계획', 'PLANNER',  crypt('1234', gen_salt('bf'))),
  ('buyer',    '박구매', 'BUYER',    crypt('1234', gen_salt('bf'))),
  ('approver', '정팀장', 'APPROVER', crypt('1234', gen_salt('bf')));

-- ------------------------------------------------------------------
-- 협력사: 기획안 3곳 + 대체 2곳 [변경]
-- 대체 협력사가 있어야 SC4의 "다른 협력사에서 긴급 구매"를 비용·위험으로 비교할 수 있다
-- ------------------------------------------------------------------
INSERT INTO supplier VALUES
  ('SUP-SEMION',  '세미온반도체', true),
  ('SUP-POWER',   '파워링크전자', true),
  ('SUP-HANGYUL', '한결정밀',     true),
  ('SUP-NURI',    '누리전자',     false),   -- [변경] 릴레이·전원 모듈 대체. 재고 보유 유통사라 빠르고 비싸다
  ('SUP-CORE',    '코어칩스',     false);   -- [변경] MCU·Wi-Fi 칩 대체. 브로커 물량이라 비싸고 품질 편차가 있다

-- ------------------------------------------------------------------
-- 품목
-- 완제품 4 · 반제품 6 · 구매 부품 28 (공용 17)
-- 리드타임: 기획안의 주 단위를 일로 바꿨다 (8주=56, 4주=28, 3주=21, 2주=14, 1주=7)
-- MOQ는 기획안에 없다 [변경]. 진행 중 발주 수량이 모두 MOQ 이상이 되게 잡았다
-- ------------------------------------------------------------------
INSERT INTO item (item_id, name, type) VALUES
  ('FG-PLUG',   '스마트 플러그',   'FG'),
  ('FG-SWITCH', '스마트 벽스위치', 'FG'),
  ('FG-DOOR',   '도어 센서',       'FG'),
  ('FG-HUB',    'IoT 허브',        'FG'),
  -- 반제품: 기획안 3.3의 허브 예시(메인보드 → 무선모듈 → 칩)를 4종 모두에 같은 모양으로 넓혔다 [변경]
  ('ASSY-PLUG-MB',   '플러그 메인보드',   'ASSY'),
  ('ASSY-SWITCH-MB', '스위치 메인보드',   'ASSY'),
  ('ASSY-DOOR-MB',   '도어센서 메인보드', 'ASSY'),
  ('ASSY-HUB-MB',    '허브 메인보드',     'ASSY'),
  ('ASSY-WIFI-MOD',  'Wi-Fi 모듈',        'ASSY'),
  ('ASSY-BLE-MOD',   'BLE 모듈',          'ASSY');

INSERT INTO item (item_id, name, type, lead_time_days, moq, safety_stock, high_value, supplier_id) VALUES
  -- 세미온반도체 (8주)
  ('MCU',          'MCU',              'PART', 56,  500,   300, true,  'SUP-SEMION'),
  ('WIFI-CHIP',    'Wi-Fi 칩',         'PART', 56,  400,   200, true,  'SUP-SEMION'),
  ('BLE-CHIP',     'BLE 칩',           'PART', 56,  400,   200, true,  'SUP-SEMION'),
  ('HALL-SENSOR',  '홀 센서',          'PART', 56,  400,   100, true,  'SUP-SEMION'),
  -- 파워링크전자 (2~4주)
  ('POWER-MODULE', '전원 모듈',        'PART', 28,  500,   100, true,  'SUP-POWER'),
  ('RELAY',        '릴레이',           'PART', 28,  500,   200, true,  'SUP-POWER'),
  ('ADAPTER',      '어댑터',           'PART', 28,  200,    50, true,  'SUP-POWER'),
  ('COIN-CELL',    '코인전지',         'PART', 28,  400,   200, true,  'SUP-POWER'),
  ('POWER-IC',     '전원 칩',          'PART', 28, 1000,   300, false, 'SUP-POWER'),
  ('USB-C',        'USB-C 커넥터',     'PART', 28, 1000,   300, false, 'SUP-POWER'),
  ('CELL-HOLDER',  '전지 홀더',        'PART', 28, 1000,   300, false, 'SUP-POWER'),
  ('CRYSTAL',      '수정진동자',       'PART', 14, 1000,   300, false, 'SUP-POWER'),
  ('CHIP-ANTENNA', '칩 안테나',        'PART', 14, 1000,   300, false, 'SUP-POWER'),
  ('CAPACITOR',    '캐패시터',         'PART', 14, 5000,   300, false, 'SUP-POWER'),
  ('RESISTOR',     '저항',             'PART', 14, 5000,   300, false, 'SUP-POWER'),
  ('LED',          'LED',              'PART', 14, 1000,   300, false, 'SUP-POWER'),
  -- 한결정밀 (1~3주)
  ('CASE-PLUG',    '플러그 케이스',      'PART', 21,  300,  100, true,  'SUP-HANGYUL'),
  ('CASE-SWITCH',  '스위치 케이스',      'PART', 21,  300,  100, true,  'SUP-HANGYUL'),
  ('CASE-DOOR-V1', '도어센서 옛 케이스', 'PART', 21,  300,    0, true,  'SUP-HANGYUL'),
  ('CASE-DOOR-V2', '도어센서 새 케이스', 'PART', 21,  300,    0, true,  'SUP-HANGYUL'),
  ('CASE-HUB',     '허브 케이스',        'PART', 21,  300,   50, true,  'SUP-HANGYUL'),
  ('BUTTON-CAP',   '버튼 캡',            'PART', 21, 1000,  300, false, 'SUP-HANGYUL'),
  ('MAGNET',       '자석',               'PART', 21, 1000,  300, false, 'SUP-HANGYUL'),
  ('SCREW',        '나사',               'PART', 21, 5000,  300, false, 'SUP-HANGYUL'),
  ('BOX-S',        '박스(소)',           'PART',  7,  500,  300, false, 'SUP-HANGYUL'),
  ('BOX-M',        '박스(중)',           'PART',  7,  500,  300, false, 'SUP-HANGYUL'),
  ('MANUAL',       '설명서',             'PART',  7, 1000,  300, false, 'SUP-HANGYUL'),
  ('LABEL',        '라벨',               'PART',  7, 1000,  300, false, 'SUP-HANGYUL');

-- ------------------------------------------------------------------
-- BOM 3단계: 완제품 → 메인보드 → 무선모듈 → 칩
-- 펼쳐서 구매 부품 단위로 더하면 기획안 4.2의 "1대당 개수"와 정확히 같다 (허브는 16종, 기획안 1.1)
-- 무선모듈 = 칩 1 + 칩 안테나 1 + 수정진동자 1 + 캐패시터 3 + 저항 1
--   → 모듈이 2개인 허브만 안테나·수정진동자가 2개인 이유가 여기서 나온다
-- ------------------------------------------------------------------
INSERT INTO bom (parent_id, child_id, qty_per) VALUES
  ('ASSY-WIFI-MOD', 'WIFI-CHIP', 1), ('ASSY-WIFI-MOD', 'CHIP-ANTENNA', 1), ('ASSY-WIFI-MOD', 'CRYSTAL', 1),
  ('ASSY-WIFI-MOD', 'CAPACITOR', 3), ('ASSY-WIFI-MOD', 'RESISTOR', 1),
  ('ASSY-BLE-MOD',  'BLE-CHIP', 1),  ('ASSY-BLE-MOD',  'CHIP-ANTENNA', 1), ('ASSY-BLE-MOD',  'CRYSTAL', 1),
  ('ASSY-BLE-MOD',  'CAPACITOR', 3), ('ASSY-BLE-MOD',  'RESISTOR', 1),

  -- 플러그: MCU·Wi-Fi·전원모듈·릴레이1·전원칩1·수정1·안테나1·캐패시터14·저항6·LED1 / 케이스·버튼1·나사2·박스(소)·설명서·라벨
  ('ASSY-PLUG-MB', 'MCU', 1), ('ASSY-PLUG-MB', 'POWER-MODULE', 1), ('ASSY-PLUG-MB', 'RELAY', 1),
  ('ASSY-PLUG-MB', 'POWER-IC', 1), ('ASSY-PLUG-MB', 'CAPACITOR', 11), ('ASSY-PLUG-MB', 'RESISTOR', 5),
  ('ASSY-PLUG-MB', 'LED', 1), ('ASSY-PLUG-MB', 'ASSY-WIFI-MOD', 1),
  ('FG-PLUG', 'ASSY-PLUG-MB', 1), ('FG-PLUG', 'CASE-PLUG', 1), ('FG-PLUG', 'BUTTON-CAP', 1),
  ('FG-PLUG', 'SCREW', 2), ('FG-PLUG', 'BOX-S', 1), ('FG-PLUG', 'MANUAL', 1), ('FG-PLUG', 'LABEL', 1),

  -- 스위치: 플러그와 같고 릴레이 2 · 버튼 2 · 나사 4 · 박스(중)
  ('ASSY-SWITCH-MB', 'MCU', 1), ('ASSY-SWITCH-MB', 'POWER-MODULE', 1), ('ASSY-SWITCH-MB', 'RELAY', 2),
  ('ASSY-SWITCH-MB', 'POWER-IC', 1), ('ASSY-SWITCH-MB', 'CAPACITOR', 11), ('ASSY-SWITCH-MB', 'RESISTOR', 5),
  ('ASSY-SWITCH-MB', 'LED', 1), ('ASSY-SWITCH-MB', 'ASSY-WIFI-MOD', 1),
  ('FG-SWITCH', 'ASSY-SWITCH-MB', 1), ('FG-SWITCH', 'CASE-SWITCH', 1), ('FG-SWITCH', 'BUTTON-CAP', 2),
  ('FG-SWITCH', 'SCREW', 4), ('FG-SWITCH', 'BOX-M', 1), ('FG-SWITCH', 'MANUAL', 1), ('FG-SWITCH', 'LABEL', 1),

  -- 도어센서: MCU·BLE·홀센서·전지홀더·전원칩1·캐패시터8·저항4·LED1 / 케이스·자석·코인전지·박스(소)·설명서·라벨
  ('ASSY-DOOR-MB', 'MCU', 1), ('ASSY-DOOR-MB', 'HALL-SENSOR', 1), ('ASSY-DOOR-MB', 'CELL-HOLDER', 1),
  ('ASSY-DOOR-MB', 'POWER-IC', 1), ('ASSY-DOOR-MB', 'CAPACITOR', 5), ('ASSY-DOOR-MB', 'RESISTOR', 3),
  ('ASSY-DOOR-MB', 'LED', 1), ('ASSY-DOOR-MB', 'ASSY-BLE-MOD', 1),
  ('FG-DOOR', 'ASSY-DOOR-MB', 1), ('FG-DOOR', 'MAGNET', 1), ('FG-DOOR', 'COIN-CELL', 1),
  ('FG-DOOR', 'BOX-S', 1), ('FG-DOOR', 'MANUAL', 1), ('FG-DOOR', 'LABEL', 1),

  -- 허브: MCU·Wi-Fi·BLE·어댑터·전원칩2·USB-C·수정2·안테나2·캐패시터19·저항8·LED1 / 케이스·나사4·박스(중)·설명서·라벨
  ('ASSY-HUB-MB', 'MCU', 1), ('ASSY-HUB-MB', 'POWER-IC', 2), ('ASSY-HUB-MB', 'USB-C', 1),
  ('ASSY-HUB-MB', 'CAPACITOR', 13), ('ASSY-HUB-MB', 'RESISTOR', 6), ('ASSY-HUB-MB', 'LED', 1),
  ('ASSY-HUB-MB', 'ASSY-WIFI-MOD', 1), ('ASSY-HUB-MB', 'ASSY-BLE-MOD', 1),
  ('FG-HUB', 'ASSY-HUB-MB', 1), ('FG-HUB', 'CASE-HUB', 1), ('FG-HUB', 'ADAPTER', 1),
  ('FG-HUB', 'SCREW', 4), ('FG-HUB', 'BOX-M', 1), ('FG-HUB', 'MANUAL', 1), ('FG-HUB', 'LABEL', 1);

-- 도어센서 케이스 설계변경 (ECN): 11/30 생산분까지 옛 케이스, 12/1 생산분부터 새 케이스 (기획안 3.2·4.2)
INSERT INTO bom (parent_id, child_id, qty_per, valid_from, valid_to) VALUES
  ('FG-DOOR', 'CASE-DOOR-V1', 1, NULL,         '2026-11-30'),
  ('FG-DOOR', 'CASE-DOOR-V2', 1, '2026-12-01', NULL);

-- ------------------------------------------------------------------
-- 현재 재고 (11/2 기준, 기획안 4.2)
-- 완제품 재고는 출하 대기 물량이라 부품 계산에 쓰지 않는다 (생산계획이 이미 이걸 빼고 세워졌다)
-- ------------------------------------------------------------------
INSERT INTO inventory VALUES
  ('FG-PLUG', 150), ('FG-SWITCH', 80), ('FG-DOOR', 120), ('FG-HUB', 40),
  ('MCU', 2700), ('WIFI-CHIP', 200), ('BLE-CHIP', 200), ('HALL-SENSOR', 100),
  ('POWER-MODULE', 100), ('RELAY', 200), ('ADAPTER', 50), ('COIN-CELL', 200),
  ('POWER-IC', 5500), ('USB-C', 700), ('CELL-HOLDER', 1700), ('CRYSTAL', 5500), ('CHIP-ANTENNA', 5500),
  ('CAPACITOR', 61100), ('RESISTOR', 27100), ('LED', 5100),
  ('CASE-PLUG', 100), ('CASE-SWITCH', 100), ('CASE-DOOR-V1', 0), ('CASE-DOOR-V2', 0), ('CASE-HUB', 50),
  -- [변경] 버튼 캡 4,500→5,000 · 나사 10,300→11,000 · 박스(중) 1,900→2,500
  -- 기획안 값이면 SC2(스위치 +300대)에서 1/11에 나사가 300개 모자라고 버튼 캡·박스(중)가 0이 된다.
  -- 기획안은 "소모품은 넉넉하다"고 가정했는데 실제로는 끝에 안전재고만 남게 맞춰져 있었다. 가정 쪽에 숫자를 맞췄다
  ('BUTTON-CAP', 5000), ('MAGNET', 1700), ('SCREW', 11000),
  ('BOX-S', 3500), ('BOX-M', 2500), ('MANUAL', 5100), ('LABEL', 5100);

-- ------------------------------------------------------------------
-- 생산 계획 ①~⑦ (기획안 4.1, 변경 전)
-- ------------------------------------------------------------------
INSERT INTO production_plan (plan_id, product_id, plan_date, qty) VALUES
  ('PP-01', 'FG-PLUG',   '2026-11-16', 1000),
  ('PP-02', 'FG-DOOR',   '2026-11-18',  800),   -- SC3: 1/4로 미룸
  ('PP-03', 'FG-SWITCH', '2026-11-23',  600),   -- SC2: 900대 / SC4: 릴레이 입고 지연
  ('PP-04', 'FG-PLUG',   '2026-11-30',  800),   -- SC1: MCU를 뺏김 / SC2: 500대
  ('PP-05', 'FG-HUB',    '2026-12-14',  400),   -- SC1: 11/25로 당김
  ('PP-06', 'FG-SWITCH', '2027-01-11',  600),
  ('PP-07', 'FG-DOOR',   '2027-01-25',  600);

-- ------------------------------------------------------------------
-- 진행 중인 발주 31건 (기획안 4.3). 모두 고가 부품
-- 발주 번호·발주일은 기획안에 없다 [변경]. 반도체는 9월, 나머지는 10월에 냈다고 보고 번호를 매겼다.
-- 문서에 이미 나온 번호를 살렸다: PO-2609-001(SC1 분할 대상), PO-2610-106(SC4 지연 대상)
-- 단가는 supplier_metric의 주 협력사 단가와 같다
-- ------------------------------------------------------------------
INSERT INTO purchase_order (po_id, item_id, supplier_id, qty, unit_price, order_date, due_date) VALUES
  -- 세미온반도체, 9월 발주
  ('PO-2609-001', 'MCU',          'SUP-SEMION',   500, 3500, '2026-09-01', '2026-12-12'),  -- ⑤ 허브용. SC1 해결: 100개를 떼어 당긴다
  ('PO-2609-002', 'WIFI-CHIP',    'SUP-SEMION',  1000, 2800, '2026-09-08', '2026-11-13'),
  ('PO-2609-003', 'BLE-CHIP',     'SUP-SEMION',   800, 1200, '2026-09-10', '2026-11-15'),
  ('PO-2609-004', 'HALL-SENSOR',  'SUP-SEMION',   800,  400, '2026-09-10', '2026-11-15'),
  ('PO-2609-005', 'WIFI-CHIP',    'SUP-SEMION',   600, 2800, '2026-09-15', '2026-11-20'),
  ('PO-2609-006', 'WIFI-CHIP',    'SUP-SEMION',   400, 2800, '2026-09-15', '2026-11-20'),  -- ⑤ 허브용 (미리 받아 둠)
  ('PO-2609-007', 'BLE-CHIP',     'SUP-SEMION',   400, 1200, '2026-09-15', '2026-11-20'),  -- ⑤ 허브용
  ('PO-2609-008', 'MCU',          'SUP-SEMION',   800, 3500, '2026-09-22', '2026-11-27'),
  ('PO-2609-009', 'WIFI-CHIP',    'SUP-SEMION',   800, 2800, '2026-09-22', '2026-11-27'),
  -- 10월 발주
  ('PO-2610-101', 'POWER-MODULE', 'SUP-POWER',   1000, 2500, '2026-10-13', '2026-11-13'),
  ('PO-2610-102', 'RELAY',        'SUP-POWER',   1000,  900, '2026-10-13', '2026-11-13'),
  ('PO-2610-103', 'COIN-CELL',    'SUP-POWER',    800,  250, '2026-10-15', '2026-11-15'),
  ('PO-2610-104', 'CASE-PLUG',    'SUP-HANGYUL', 1000, 1600, '2026-10-20', '2026-11-13'),
  ('PO-2610-105', 'POWER-MODULE', 'SUP-POWER',    600, 2500, '2026-10-20', '2026-11-20'),
  ('PO-2610-106', 'RELAY',        'SUP-POWER',   1200,  900, '2026-10-20', '2026-11-20'),  -- ③ 스위치용. SC4: 11/27로 지연
  ('PO-2610-107', 'ADAPTER',      'SUP-POWER',    400, 3000, '2026-10-20', '2026-11-20'),  -- ⑤ 허브용
  ('PO-2610-108', 'MCU',          'SUP-SEMION',  1100, 3500, '2026-10-20', '2027-01-06'),
  ('PO-2610-109', 'WIFI-CHIP',    'SUP-SEMION',   600, 2800, '2026-10-22', '2027-01-08'),
  ('PO-2610-110', 'CASE-DOOR-V1', 'SUP-HANGYUL',  800,  800, '2026-10-23', '2026-11-15'),  -- SC3: 12/1 이후 못 씀
  ('PO-2610-111', 'CASE-SWITCH',  'SUP-HANGYUL',  600, 1500, '2026-10-28', '2026-11-20'),
  ('PO-2610-112', 'CASE-HUB',     'SUP-HANGYUL',  400, 2500, '2026-10-28', '2026-11-20'),  -- ⑤ 허브용
  ('PO-2610-113', 'POWER-MODULE', 'SUP-POWER',    800, 2500, '2026-10-28', '2026-11-27'),
  ('PO-2610-114', 'RELAY',        'SUP-POWER',    800,  900, '2026-10-28', '2026-11-27'),
  ('PO-2610-115', 'CASE-PLUG',    'SUP-HANGYUL',  800, 1600, '2026-10-30', '2026-11-27'),  -- SC2: 500개만 쓰고 300개 남음
  ('PO-2610-116', 'BLE-CHIP',     'SUP-SEMION',   600, 1200, '2026-10-30', '2027-01-22'),
  ('PO-2610-117', 'HALL-SENSOR',  'SUP-SEMION',   600,  400, '2026-10-30', '2027-01-22'),
  ('PO-2610-118', 'POWER-MODULE', 'SUP-POWER',    600, 2500, '2026-10-30', '2027-01-08'),
  ('PO-2610-119', 'RELAY',        'SUP-POWER',   1200,  900, '2026-10-30', '2027-01-08'),
  ('PO-2610-120', 'CASE-SWITCH',  'SUP-HANGYUL',  600, 1500, '2026-10-30', '2027-01-08'),
  ('PO-2610-121', 'COIN-CELL',    'SUP-POWER',    600,  250, '2026-10-30', '2027-01-22'),
  ('PO-2610-122', 'CASE-DOOR-V2', 'SUP-HANGYUL',  600,  800, '2026-10-30', '2027-01-22');

-- 이미 입고된 지난 발주 1건. 현재 재고 2,700에 이미 들어가 있으므로 계산에는 영향이 없다 (계산은 OPEN만 본다).
-- 변경 도구에 넣었을 때 PO_NOT_OPEN 오류가 나는지 시험하는 용도다 [변경]
INSERT INTO purchase_order (po_id, item_id, supplier_id, qty, unit_price, order_date, due_date, status) VALUES
  ('PO-2608-014', 'MCU', 'SUP-SEMION', 300, 3500, '2026-08-25', '2026-10-28', 'RECEIVED');

-- ------------------------------------------------------------------
-- 협력사 평가지표 (decisions.md 2.2). 전부 기획안에 없는 값이다 [변경]
-- 단가 중 기획안 금액 표에서 거꾸로 나오는 것은 그 값을 썼다:
--   BLE 칩 1,200 · 홀 센서 400 · 코인전지 250 · 옛 케이스 800 · 플러그 케이스 1,600 (원)
-- 나머지 단가는 고가/소모품 구분이 기획안과 맞게 잡았다.
-- 납기 준수율: 파워링크는 SC4에서 실제로 지연을 내므로 가장 낮게 둔다
-- ------------------------------------------------------------------
INSERT INTO supplier_metric VALUES
  -- 세미온반도체: 긴급 당김 1일 20,000원 → SC1 분할(12/12→11/25, 17일) = 340,000 + 분할 50,000
  ('SUP-SEMION', 'MCU',          3500, 56, 20000, 50000, 300000, 92.0, 95, '2026-10-15', 'buyer'),
  ('SUP-SEMION', 'WIFI-CHIP',    2800, 56, 20000, 50000, 300000, 92.0, 95, '2026-10-15', 'buyer'),
  ('SUP-SEMION', 'BLE-CHIP',     1200, 56, 15000, 50000, 200000, 92.0, 95, '2026-10-15', 'buyer'),
  ('SUP-SEMION', 'HALL-SENSOR',   400, 56, 10000, 50000, 100000, 92.0, 94, '2026-10-15', 'buyer'),
  -- 파워링크전자
  ('SUP-POWER', 'POWER-MODULE',  2500, 28, 15000, 30000, 200000, 78.0, 90, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'RELAY',          900, 28, 10000, 30000, 150000, 78.0, 88, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'ADAPTER',       3000, 28, 10000, 30000, 100000, 78.0, 90, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'COIN-CELL',      250, 28,  5000, 30000,  50000, 78.0, 92, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'POWER-IC',       300, 28,  3000, 30000,  30000, 78.0, 93, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'USB-C',          250, 28,  3000, 30000,  30000, 78.0, 93, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'CELL-HOLDER',     80, 28,  2000, 30000,  20000, 78.0, 95, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'CRYSTAL',        120, 14,  2000, 30000,  20000, 78.0, 95, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'CHIP-ANTENNA',   150, 14,  2000, 30000,  20000, 78.0, 95, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'CAPACITOR',       10, 14,  2000, 30000,  20000, 78.0, 96, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'RESISTOR',         5, 14,  2000, 30000,  20000, 78.0, 96, '2026-10-15', 'buyer'),
  ('SUP-POWER', 'LED',             30, 14,  2000, 30000,  20000, 78.0, 95, '2026-10-15', 'buyer'),
  -- 한결정밀
  ('SUP-HANGYUL', 'CASE-PLUG',    1600, 21, 10000, 20000, 100000, 95.0, 97, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'CASE-SWITCH',  1500, 21, 10000, 20000, 100000, 95.0, 97, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'CASE-DOOR-V1',  800, 21,  8000, 20000,  80000, 95.0, 97, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'CASE-DOOR-V2',  800, 21,  8000, 20000,  80000, 95.0, 97, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'CASE-HUB',     2500, 21, 10000, 20000, 100000, 95.0, 97, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'BUTTON-CAP',     50, 21,  2000, 20000,  20000, 95.0, 98, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'MAGNET',         60, 21,  2000, 20000,  20000, 95.0, 98, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'SCREW',          10, 21,  2000, 20000,  20000, 95.0, 98, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'BOX-S',         200,  7,  2000, 20000,  20000, 95.0, 98, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'BOX-M',         300,  7,  2000, 20000,  20000, 95.0, 98, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'MANUAL',        100,  7,  2000, 20000,  20000, 95.0, 98, '2026-10-15', 'buyer'),
  ('SUP-HANGYUL', 'LABEL',          20,  7,  2000, 20000,  20000, 95.0, 98, '2026-10-15', 'buyer'),
  -- 대체 협력사 [변경]: 리드타임이 짧은 대신 단가가 높고 준수율·품질이 낮다
  ('SUP-NURI', 'RELAY',          1400,  7, 10000, 30000, 150000, 85.0, 85, '2026-10-15', 'buyer'),  -- SC4: 11/9까지 받을 수 있다
  ('SUP-NURI', 'POWER-MODULE',   3600, 10, 15000, 30000, 200000, 85.0, 85, '2026-10-15', 'buyer'),
  ('SUP-CORE', 'MCU',            5500, 14, 20000, 50000, 300000, 80.0, 82, '2026-10-15', 'buyer'),  -- SC1: 11/16까지 받을 수 있다
  ('SUP-CORE', 'WIFI-CHIP',      4200, 14, 20000, 50000, 300000, 80.0, 82, '2026-10-15', 'buyer');

RESET ROLE;
