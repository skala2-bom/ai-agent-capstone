# 참고용 — 그대로 쓰지 않는다

이 폴더는 팀에서 받은 "가온스마트 상세 목데이터 v2" 원본이다 (`README.md`는 원본 설명).

- **ID 체계가 지금 시스템과 다르다**: `FG100`·`P-MCU01`·`PO-DEMO-001`·`SUP01` ↔ DB의 `FG-PLUG`·`MCU`·`PO-2609-001`·`SUP-SEMION`.
- **DB에 넣지 않는다.** 시연 데이터는 `db/seed.sql`이다.
- **평가 정답으로 쓰지 않는다.** `scenarios.json`의 SC3 정답(BLE 칩·홀 센서 과잉)은 과잉 기간 규칙(decisions.md 1.3)과 다르다. 정답은 `eval/golden.json`.
- RAG 원문은 시스템에 맞춰 고친 판이 `data/docs/`에 있다. `docs-original/`은 고치기 전 원본이다 — **색인하지 않는다.**
- 로트·입고 검수·작업지시·해결안 등급(`operations.json`·`resolution_options.json`)은 프론트에만 있는 업무를 뒷받침하는 자료다. backend에 둘지는 팀 결정 대기 (decisions.md 5절 14번).
