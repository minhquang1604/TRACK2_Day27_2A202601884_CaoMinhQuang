# AI Agent Decision Log

Khong can copy full conversation. Ghi cac decision quan trong.

## CP0 — Baseline & system understanding

**Cau hoi mo dau:**
- Dataset critical nhat: `orders` (source cua revenue va cua contract critical checks); `customers` critical vi la dimension join cho `fct_daily_revenue` (co the gay revenue inflation neu co >1 active row/customer). `kb_documents` critical cho Support Agent (RAG), it duoc chu y hon nhung anh huong truc tiep noi dung tra loi khach hang.
- Downstream consumer: CEO dashboard (qua `fct_daily_revenue` -> `ceo_revenue_dashboard`) va Support Agent (qua `kb_documents` -> `kb_active_docs` -> `rag_index` -> `support_agent`).
- Metric bao hieu data khong dang tin: pipeline status luon `SUCCESS` (khong tu fail khi data sai), nen phai dua vao: `contract failed checks`/`critical contract fails`, `row-count anomaly`, `freshness minutes`, va (sau nay) SLO burn rate — khong dataset nao tu bao "toi sai".

## Decision 1
- Hypothesis: Sau `make reset` + `make baseline`, he thong o trang thai khoe nen tat ca signal phai "OK".
- Prompt / request to agent: Chay `make reset`, `make baseline`, `pytest tests_public -q` va doc `reports/latest_metrics.json` de hieu he thong.
- Agent proposal: Khong co code thay doi o CP0, chi quan sat.
- Evidence/test: `pytest tests_public -q` -> 10 passed. Nhung `make baseline` bao `row-count anomaly: True (auto:zscore, score=27.23)` ngay ca khi data la baseline "khoe". Dieu tra: hom nay (chay that, 2026-08-29) la Thu Bay (`weekday()==5`), trong khi `metrics_history.csv` co seasonality that (cuoi tuan ~43% volume). `run_baseline.py` lay segment lich su theo `day_of_week` hien tai (~235-268 don) de so voi 600 don hien tai (data incoming khong doi theo ngay that) -> zscore rat cao mot cach gia tao.
- Accept / reject / revise: Ghi nhan day la **false positive co that** cua starter `auto:zscore`, khong phai loi cua CP0. Se la evidence chinh cho CP3 (auto mode phai context-aware / robust voi seasonality thay vi zscore tho).
- Why: Day dung tinh than lab — "pipeline SUCCESS khong co nghia data dung", va o day con la "detector bao anomaly khong co nghia data thuc su sai" (false alarm can phan tich truoc khi tin).

## CP1 — Data contract + validation

## Decision 2
- Hypothesis: `validate_dataframe` (src/contract_validator.py) hien dung `pd.to_numeric(errors="coerce")` cho ca range va (khong co) type check, nen mot gia tri sai kieu (vd amount="abc") bi am tham bien thanh NaN roi lot qua het cac check thay vi bi bao loi ro rang. Contract YAML da khai bao `freshness` (updated_at, max_delay_minutes=30) nhung validator chua doc field nay.
- Prompt / request to agent: Them type validation tuong minh theo `rules['type']` (integer/number/string/datetime), them freshness check dung `contract['freshness']`, va gan severity->action (critical=block, warning=quarantine, info=warn) vao moi issue ma khong pha shape {check, column, severity, passed, details} trong docs/STUDENT_API.md.
- Agent proposal: Them `_type_invalid_mask()` (per-type check, rieng "string" flag ca cot neu dtype bi doc nham thanh numeric/bool), `_validate_freshness()` (so `now` UTC voi max(updated_at), fail neu delay > max_delay_minutes), va field `action` tren moi issue + ham `overall_action(issues)` de tong hop quyet dinh worst-case cho ca batch.
- Evidence/test: `pytest tests_public -q` phat hien `test_healthy_contract_passes_starter_checks` FAIL ngay khi them freshness — ly do: fixture `healthy_df()` trong tests_public/test_contracts.py dung ngay gio **hardcoded** ("2026-08-28T10:00:00Z"), nen se luon "stale" so voi `now` thuc te bat ke chay ngay nao. Sua fixture sang dung `datetime.now(timezone.utc) - timedelta(...)` (tuong doi, khong hardcode) — dung dung ban chat cua freshness test. Sau khi sua: `pytest tests_public -q` -> 12 passed (them 2 test moi: type drift tren `amount`, freshness tren `updated_at`). Chay `python scripts/inject_fault.py duplicate_pk` -> `make baseline` bao `contract failed checks: 1` dung 1 loi `unique`, khong co false positive tu type/freshness moi them. `make reset` -> lai 0 loi.
- Accept / reject / revise: Accept ca 2 thay doi (contract_validator.py va sua fixture test_contracts.py). Sua fixture la quyet dinh co chu y, khong phai "sua test cho qua" — giu nguyen y nghia goc cua test (healthy data -> khong loi) nhung lam no dung voi thoi gian thuc.
- Why: Freshness ban chat la so sanh voi "now" nen fixture phai tuong doi; giu nguyen hardcoded date se lam public test tu hong sau vai ngay du code dung, day la mot bug an trong starter test can sua khi implement TODO freshness.

## Decision 3
- Hypothesis: `gx/validate_orders.py` hien chi chay 4 expectation roi tao PASS/FAIL don gian, chua co Suite/ValidationDefinition/Checkpoint/Actions that su, va chua co severity->action giong contract_validator.
- Prompt / request to agent: Nang cap thanh GX Core 1.21 flow day du: `ExpectationSuite` + `ValidationDefinition` + `Checkpoint` + custom `ValidationAction` (subclass) anh xa severity (da co san tren tung expectation qua tham so `severity=`) sang action (block/quarantine/warn), dung chinh sach ACTION_BY_SEVERITY giong contract_validator.py.
- Agent proposal: Viet `SeverityAction(ValidationAction)` doc `expectation_config.severity` tu moi failed expectation result trong `CheckpointResult.run_results`, in ra va tra ve summary; `main()` chay checkpoint, tinh lai summary de ghi `reports/gx_validation_result.json` va in "Pipeline action: BLOCK/QUARANTINE/WARN/NONE".
- Evidence/test: Chay tren data khoe -> `GX checkpoint success: True`, `Pipeline action: NONE`. Sau `inject_fault.py duplicate_pk` -> `expect_column_values_to_be_unique` fail (severity=critical) -> in dong `[critical] ... -> action=block`, `Pipeline action: BLOCK`, dung `reports/gx_validation_result.json`.
- Accept / reject / revise: Accept. Da verify API 1.21 that (khong doan) bang cach introspect truc tiep package (`ExpectationSuite`, `ValidationDefinition`, `Checkpoint`, `ValidationAction`, `expectation_config.severity`) truoc khi viet code, tranh loi API sai phien ban.
- Why: Rubric co dong rieng "Great Expectations hoac equivalent validation flow: 10d" + bonus "GX severity/actions: +3" — can flow that (Suite/Checkpoint/Actions), khong chi 4 expectation roi le.

## CP2 — dbt transformation protection

**Vi sao `not_null`/`unique` la data test chu khong phai unit test** (yeu cau bat buoc cua Phase 2): data test (generic: not_null/unique/accepted_values/relationships, hoac singular SQL) chay tren **du lieu that** trong warehouse sau khi model da build — no tra loi "du lieu hien tai co vi pham gia dinh khong", va co the pass/fail khac nhau moi lan chay tuy input thay doi, du logic SQL khong doi. Unit test (`unit_tests.yml`, dbt >=1.8) chay tren **input gia lap co dinh** (`given: rows: [...]`) doc lap voi du lieu that trong warehouse, va so voi `expect: rows: [...]` — no tra loi "logic transformation (SQL) co dung khong", ket qua deterministic 100% va khong phu thuoc seed/fault nao dang co trong `data/incoming`. Vi du cu the trong repo nay: `unique_fct_daily_revenue_order_date` (data test) se **pass** ngay ca khi model co bug fanout, mien la du lieu hien tai tinh co khong co 2 active row/customer nao; chi unit test `duplicate_active_customer_row_does_not_inflate_revenue` moi bat bug do **luon luon**, vi no tu dung nen du lieu loi thay vi cho no "tinh co" xuat hien.

## Decision 4
- Hypothesis: Comment co san trong `fct_daily_revenue.sql` canh bao ro: neu customer dimension co >1 active row/customer_id, LEFT JOIN se fan-out va nhan doi revenue ma khong co loi SQL nao. Day la bug that, chua co test nao bat duoc.
- Prompt / request to agent: Viet dbt unit test nho nhat de "expose" bug nay (theo dung prompt mau trong docs/AI_AGENT_GUIDE.md), KHONG sua model truoc, chay de xac nhan FAIL, roi moi sua model va chay lai de xac nhan PASS.
- Agent proposal: Doi `unit_tests.yml.example` -> `unit_tests.yml` that, them 2 unit test: (1) sanity 2 don cung customer -> tong dung; (2) `duplicate_active_customer_row_does_not_inflate_revenue`: 1 don completed amount=100, nhung stg_customers co 2 row active cung customer_id -> expect completed_order_rows=1, daily_revenue=100.0 (khong duoc nhan doi).
- Evidence/test: Chay `dbt test --select test_type:unit` tren model **goc** -> test (2) FAIL that: `actual` tra ve `completed_order_rows=2, daily_revenue=200.0` thay vi 1/100.0 — dung y bug da canh bao. Sau do sua `active_customers` CTE them `qualify row_number() over (partition by customer_id order by valid_from desc) = 1` de luon giu dung 1 active row/customer (DuckDB ho tro QUALIFY, da tu verify truc tiep truoc khi dung). Chay lai `make dbt` -> ca 2 unit test PASS, toan bo 21/21 (them 2 generic test: `unique` tren `fct_daily_revenue.order_date`, `relationships` tu `stg_orders.customer_id` -> `stg_customers.customer_id`, `accepted_values` tren `stg_customers.tier`; them 1 singular test moi `assert_completed_order_rows_match_stg_orders.sql` doi chieu completed_order_rows voi dem truc tiep tu stg_orders — bao ve tren du lieu that, khong chi fixture co dinh).
- Accept / reject / revise: Accept fix model (khac voi vi du prompt trong AI_AGENT_GUIDE la "chua sua model", nhung LAB_GUIDE Phase 2 goi day la "dbt transformation **correctness**" — TDD workflow viet test FAIL truoc -> fix -> PASS moi la evidence day du, khong chi dung lai o buoc "expose").
- Why: Mot data pipeline khong nen "biet" no co the tinh sai revenue ma khong sua; giu bug lai chi de "cho hidden eval bat" la rui ro khong can thiet, trong khi da co evidence ro rang (before/after) de chung minh fix dung, khong phai doan mo.

- pytest tests_public -q: 12 passed (khong bi anh huong boi thay doi dbt). `make dbt`: PASS=21 TOTAL=21, khong con deprecation warning (sua `relationships` sang dung `arguments:` nested giong style san co trong repo).

## CP3 — Anomaly detection

**Khi nao z-score sai** (yeu cau bat buoc, giai thich): (1) history nho (n<~10) lam mean/std uoc luong khong on dinh; (2) history co outlier (1 ngay spike/outage) keo mean/std lech, lam nguong bi "pha loang" -> co the bo sot anomaly that hoac bao nham ngay binh thuong; (3) phan phoi lech (vd log-normal cua `amount`) khien "3 std tu mean" khong con y nghia xac suat chuan; (4) quan trong nhat trong repo nay: z-score/MAD deu khong sua duoc loi **sai nhom so sanh** — neu `current` va `history` khong cung "segment" (vd so 1 ngay full-volume voi baseline chi co du lieu cuoi tuan), moi thong ke deu se bao anomaly gia du logic thong ke dung 100%. Day chinh la nguyen nhan cua false positive da phat hien o CP0.

## Decision 5
- Hypothesis: `detect_anomaly(method="auto")` hien tai bo qua hoan toan `context` va luon dung z-score/mean/std tho — se bao sai (1) khi history co outlier, va (2) khong tan dung duoc `context["same_segment_history"]` ma STUDENT_API mo ta.
- Prompt / request to agent: Nang cap `auto`: uu tien `context["same_segment_history"]` lam baseline neu co (>=3 diem), dung robust median/MAD khi du du lieu (>=5 diem, giong nguong cua `mad_detector`), fallback z-score khi history ngan hoac MAD suy bien (0). Khong xoa `zscore_detector`/goi method="zscore" rieng (giu nguyen hanh vi cu). Kem sua `mad_detector`'s "mad_is_zero_todo": history hang so + current khac -> anomaly (score=inf); current bang -> khong anomaly.
- Agent proposal: Them `_auto_baseline()` chon `same_segment_history` hoac fallback `history`; `detect_anomaly("auto")` goi `mad_detector` khi >=5 diem (tin tuong hoan toan vi mad_detector da tu xu ly ca truong hop mad=0), fallback zscore khi <5 diem; `context["known_event"]` duoc ghi vao `reason` de trace nhung KHONG tu dong tat canh bao (tranh che giau incident that chi vi co nhan "known_event").
- Evidence/test: Verify thu cong 5 case (Saturday hop le voi same_segment -> False; giam 70% van trong cung segment -> True; mad=0 + current khac -> True/inf; mad=0 + current bang -> False; known_event xuat hien trong reason nhung khong doi is_anomaly). Chay `inject_fault.py volume_drop` (con 150/600 don, giam 75%) -> `row-count anomaly: True (auto:mad, score=23.53)` — dat yeu cau bat buoc Phase 3. Them 4 test moi vao tests_public/test_anomaly.py (Saturday khong bi flag, giam that trong cung segment van bi flag, MAD zero-edge ca 2 chieu) -> `pytest tests_public -q`: 15 passed.
- Accept / reject / revise: Accept toan bo thay doi trong observability/anomaly.py.
- Why: Dung tinh than Phase 3 "Nang cap auto de xu ly seasonality/outlier ... khong can ML phuc tap neu statistical baseline da giai quyet dung bai toan" — median/MAD + context-aware segment la du, khong can model ML.

## Decision 6 — Fix false positive tu CP0 (root cause, khong chi trieu chung)
- Hypothesis: False positive o CP0 (baseline khoe nhung `row-count anomaly: True score=27.23`) khong phai loi thong ke (da thu MAD o Decision 5, van con anomaly vi sai nhom so sanh) ma la loi **chon sai segment** trong `scripts/run_baseline.py`: script dung `datetime.now().weekday()` (ngay thuc te luc chay) de loc history, nhung `scripts/generate_data.py` luon sinh "du lieu hom nay" o muc volume weekday day du (khong scale giam cuoi tuan), bat ke ngay thuc te la thu may. Nen khi chay lab vao thu Bay/Chu Nhat that, script tu so sanh sai nhom.
- Prompt / request to agent: Sua `run_baseline.py` de segment theo **weekday-class** (Mon-Fri) thay vi ngay thuc trong tuan, va truyen `same_segment_history` qua context dung theo STUDENT_API thay vi chi pre-filter phia caller.
- Agent proposal: Doi dieu kien loc tu `history.day_of_week == current_dow` sang `history.day_of_week < 5` (tail 10), giu `current_dow` trong context de quan sat/debug nhung khong dung de loc nua; them `same_segment_history` vao context truyen cho `detect_anomaly`.
- Evidence/test: `make reset` -> `make baseline`: `row-count anomaly: False (auto:mad, score=0.76)` (het false positive). `inject_fault.py volume_drop` -> `make baseline`: `row-count anomaly: True (auto:mad, score=23.53)` (van bat dung fault that). `pytest tests_public -q`: 15 passed. `make dbt`: 21/21 khong doi (khong lien quan).
- Accept / reject / revise: Accept. Day la sua script demo (`run_baseline.py`), KHONG thuoc stable API (`student_api.py`) nen khong anh huong hidden eval — chi lam bao cao local dang tin cay hon xuyen suot ca lab.
- Why: "Detector bao anomaly khong co nghia data thuc su sai" (dung tu Decision 1) — false alarm neu khong dieu tra se lam nguoi dung mat niem tin vao toan bo he thong canh bao ngay tu Phase 0, truoc khi ke ca bat dau xet fault that.

## CP4 — Lineage & Blast Radius

**Bat buoc:** `get_downstream_assets(dataset_lineage, "stg_orders")` -> `['fct_daily_revenue', 'ceo_revenue_dashboard']`. Ham nay da dung tu starter (co public test), khong can sua.

## Decision 7
- Hypothesis: `get_column_downstream` starter chi tra ve direct children (`column_graph.get(start_column, [])`), nen transitive hidden case (vd `kb_documents.content` -> `rag_index.embedding` qua 2 hop) se fail.
- Prompt / request to agent: Implement BFS transitive giong `get_downstream_assets` nhung tren `column_lineage`.
- Agent proposal: `column_lineage` co cung shape `dict[str, list[str]]` nhu `dataset_lineage` (chi khac ten node la `table.column`), nen tai su dung thang `get_downstream_assets(column_graph, start_column)` thay vi viet lai BFS — tranh trung logic.
- Evidence/test: `column_downstream({"kb_documents.content": [...], ...}, "kb_documents.content")` -> `['kb_active_docs.content', 'rag_index.embedding', 'support_agent.answer']` (dung 3 hop, khong chi 1). Them 2 test moi (transitive + fan-out khong bi trung lap khi 2 nhanh cung do ve 1 dich) vao tests_public/test_lineage.py -> pytest 17 passed. Verify them tren du lieu that: `raw_orders.amount` -> `['stg_orders.amount_usd', 'fct_daily_revenue.daily_revenue', 'ceo_revenue_dashboard.revenue']`.
- Accept / reject / revise: Accept — 1 dong sua, tai su dung ham co san, khong tang dien tich bug moi.
- Why: DRY — logic BFS/dedupe da dung va da co test cho dataset-level, khong can duplicate.

## Decision 8 (Advanced/bonus)
- Hypothesis: `extract_dbt_dataset_graph(manifest_path)` da co san trong starter nhung chua ai chay thu voi manifest that sau khi them nhieu model/test/unit test o CP2.
- Prompt / request to agent: Chay `make dbt` de sinh `dbt_project/target/manifest.json`, roi goi `extract_dbt_dataset_graph` + `get_downstream_assets` de xac nhan no hoat dong dung tren graph dbt that (khong chi dataset_lineage.json thu cong).
- Agent proposal: Khong sua code (ham da dung), chi verify.
- Evidence/test: `extract_dbt_dataset_graph('dbt_project/target/manifest.json')` -> 21 node; downstream cua `model.data_reliability_lab.stg_orders` bao gom dung `fct_daily_revenue` VA toan bo 12 data/unit test phu thuoc no (vi child_map cua dbt include ca test node) — chi tiet hon dataset_lineage.json thu cong (chi co 2 dataset), vi no phan anh dung dependency graph thuc te dbt build ra.
- Accept / reject / revise: Accept, khong can sua gi them cho phan nay.
- Why: Chung minh bonus "column lineage" da lam (Decision 7) va lop lineage tu dbt manifest (advanced) deu hoat dong, khong chi ly thuyet.

## CP5 — SLO / Error Budget

**Bat buoc — tinh tay SLO=99.5%, 2 bad/100 checks** (verify bang `slo_status`, ham nay da dung tu starter, khong sua):
- actual_bad_rate = 2/100 = 0.02
- allowed_bad_rate = 1 - 0.995 = 0.005
- burn_rate = 0.02 / 0.005 = 4.0 (dang tieu toc do gap 4 lan ngan sach cho phep)
- breached = True (0.02 > 0.005)

## Decision 9
- Hypothesis: `evaluate_multiwindow_burn` starter luon tra `page=False` (chua implement gi), can chinh sach phan biet duoc "sustained fast burn" (nen page) voi "transient spike" (khong nen page) theo dung yeu cau Phase 5.
- Prompt / request to agent: Implement multi-window burn-rate policy kieu Google SRE Workbook (https://sre.google/workbook/alerting-on-slos/): chi page khi CA HAI window (short + long) deu burn nhanh cung luc; spike ngan o short window ma long window binh thuong thi khong page; long window burn cao vua phai (khong toi muc "fast") van nen bao warning (ticket) chu khong im lang hoan toan.
- Agent proposal: Dung nguong `FAST_BURN_SHORT_THRESHOLD=14.4`, `FAST_BURN_LONG_THRESHOLD=6.0` (theo dung con so kinh dien trong tai lieu SRE cho burn rate "an het 2% budget/1h"), them `ELEVATED_LONG_BURN_THRESHOLD=1.0` cho truong hop "slow sustained burn" (severity=warning, page=False) de khong bo qua hoan toan tin hieu tieu ngan sach cham nhung deu.
- Evidence/test: Verify boundary: (14.4, 6.0) -> page=True/critical; (14.4, 5.9) -> page=False/warning "transient spike" (dung 1 don vi duoi nguong long da doi ket qua); (2.0, 0.5) -> info; (1.0, 1.5) -> warning "slow sustained burn". Them 2 test moi vao tests_public/test_slo.py (sustained fast burn -> page True; transient spike short=20/long=0.5 -> page False) -> pytest 19 passed. `make dbt`: 21/21 khong doi (khong lien quan).
- Accept / reject / revise: Accept.
- Why: Dung yeu cau ro rang cua LAB_GUIDE Phase 5 Advanced ("transient spike ngan -> khong page, sustained fast burn -> page") va bonus rubric "multi-window burn-rate: +7". Nguong lay tu tai lieu chinh thong (khong bia so tuy tien) de co the giai thich/defend duoc khi duoc hoi.

## CP6 — Mystery incident RCA

Khong co mystery dataset rieng tu giang vien trong moi truong nay, nen dung 3 fault scenario cong khai lam bai dien tap dieu tra qua toan bo layer da nang cap (contract, GX, dbt, anomaly, lineage, SLO). Truoc khi dieu tra, phat hien 1 gap: **`stale_kb` hien khong bi bat boi bat ky layer nao** — `kb_text_length_signal` chi do do dai content (khong doi khi stale_kb chi doi timestamp), va KB khong di qua `validate_orders`. Day dung "TODO co chu dich" ma LAB_GUIDE canh bao.

## Decision 10
- Hypothesis: `contracts/kb_contract.yaml` da khai bao `freshness` (column=published_at, max_delay_minutes=60) va `lab_config.yaml` da khai bao SLO `rag_index_freshness` (target=0.99, threshold_minutes=60), nhung khong co code nao noi 2 thu nay lai voi nhau.
- Prompt / request to agent: Them tinh toan KB freshness + KB freshness SLO vao `scripts/run_baseline.py` (khong dua vao contract_validator.py vi kb_contract dung shape `fields` khac voi `columns` cua orders_contract, va day la script demo/investigation, khong phai stable API), tai su dung `calculate_slo` da co.
- Agent proposal: Tinh `kb_delay_minutes` cho tung doc, dem `kb_stale_count` (doc vuot `max_delay_minutes`), goi `calculate_slo(rag_index_freshness.target, bad_events=kb_stale_count, total_events=len(docs))`, in ra report + `reports/latest_metrics.json`.
- Evidence/test: Baseline khoe -> `KB freshness (max delay): 18.0 min ... stale_docs=0/5`, `SLO breach: False`. `inject_fault.py stale_kb` -> `KB freshness (max delay): 198.1 min ... stale_docs=5/5`, `SLO breach: True (burn_rate=100.00)` — fault gio da bi bat ro rang, dung nhu ky vong cua LAB_GUIDE Phase 3 "stale_kb: Starter baseline hien chua hoan thien KB freshness/SLO — TODO co chu dich".
- Accept / reject / revise: Accept.
- Why: Mot signal SLO da duoc khai bao san trong config nhung khong bao gio duoc tinh la mot "silent gap" nguy hiem hon ca mot detector sai — no tao cam giac an toan gia (dashboard/config trong co ve day du) trong khi thuc te khong co gi giam sat.

### RCA chinh: `duplicate_pk` (chon vi cham nhieu layer nhat, dung kich ban CEO/revenue trong README)

Thiet lap: `make reset` -> `python scripts/inject_fault.py duplicate_pk` (nhan 3 dong dau cua orders.csv, 600 -> 603 dong, 3 order_id bi trung).

1. **What happened?** 3 dong orders bi duplicate order_id (cung 1 don hang xuat hien 2 lan trong data).
2. **When did it start?** Ngay sau khi file `data/incoming/orders.csv` bi ghi lai boi buoc ingest loi (trong lab: ngay sau lenh inject_fault). Trong thuc te se xac dinh qua thoi diem file duoc ghi/updated_at moi nhat khong doi (freshness van la 5.0 phut — chung to day KHONG phai loi do delay/staleness, ma la loi *chat luong* trong 1 lan ghi).
3. **Root cause?** Upstream ingestion ghi trung dong (vd retry khong idempotent). Evidence: `contract failed checks: 1` (`check=unique, column=order_id, severity=critical, action=block`); GX checkpoint doc lap xac nhan cung ket luan (`expect_column_values_to_be_unique -> action=block`); dbt `unique_stg_orders_order_id` FAIL 3 (dung 3 dong trung).
4. **Blast radius?** `downstream_assets(graph, "stg_orders")` = `['fct_daily_revenue', 'ceo_revenue_dashboard']`. Bang chung thuc te: `dbt build` tu **SKIP** toan bo 9 node phia sau (`fct_daily_revenue` + 8 test/unit-test) ngay khi `unique_stg_orders_order_id` FAIL — dbt DAG da tu chan lan truyen loi xuong CEO dashboard, dung "row-count anomaly: False" luc nay (600->603 khong du lon de bi flag rieng le) cho thay: neu chi dua vao anomaly detector se BO SOT loi nay — phai co ca contract/dbt.
5. **Mitigation?** Action=BLOCK tu ca contract_validator va GX -> pipeline khong nen chay tiep buoc dbt/publish cho toi khi orders.csv duoc dedupe lai o nguon (hoac quarantine 3 dong trung, giu lai 1 dong/order_id).
6. **Recovery verification?** Sau `make reset`: `contract failed checks: 0`, GX `success: True/action=NONE`, `make dbt` PASS=21/TOTAL=21, `pytest tests_public -q` 19 passed — toan bo 4 layer deu xac nhan da phuc hoi, khong chi 1 layer rieng le.
7. **Prevention?** (a) Ingestion layer nen idempotent theo order_id (upsert thay vi append); (b) contract `unique` + GX + dbt `unique` test da co san 3 lop bao ve doc lap — day la defense-in-depth dung nghia, nen giu ca 3 thay vi chi dua vao 1; (c) alert nen tag ro "action=block" de on-call biet ngay can chan pipeline thay vi chi xem "test FAIL".

### Dien tap phu: `volume_drop` (anomaly), `stale_kb` (freshness/SLO — xem Decision 10 o tren)
- `volume_drop`: 600->150 don (giam 75%), contract/GX/dbt deu PASS (khong co gi "sai cau truc"), chi `row-count anomaly: True (auto:mad, score=23.53)` bat duoc — minh chung ro rang cho ly do can layer anomaly rieng, vi day la loi ve **so luong/hoan thanh** (partial ingestion) chu khong phai loi cau truc/schema ma contract/dbt kiem tra duoc.

## CP7 — Report & wrap-up

`reports/incident_report.md` da duoc dien day du dua tren RCA `duplicate_pk` o CP6 (severity/summary/detection/root cause/evidence/blast radius/mitigation/recovery/verification checklist/action items), kem tham chieu sang gap `stale_kb` (Decision 10) trong phan verification.

**Tom tat 10 quyet dinh chinh (CP1-CP6), doi chieu voi SCORING.md:**

| # | CP | Thay doi | Rubric lien quan |
|---|---|---|---|
| 2 | CP1 | Type validation + freshness + severity/action trong contract_validator.py | Data contract (10d) |
| 3 | CP1 | GX Suite/ValidationDefinition/Checkpoint/Actions that | GX flow (10d) + bonus GX actions (+3) |
| 4 | CP2 | Unit test expose bug fanout -> fix `qualify row_number()` + generic/singular tests moi | dbt tests + correctness (10d) + bonus unit test (+3) |
| 5 | CP3 | `auto` context-aware (same_segment_history + median/MAD) + fix mad_is_zero | Anomaly detection (15d) + bonus MAD/same-weekday (+3) |
| 6 | CP3 | Fix root-cause false positive trong run_baseline.py (weekday-class segment) | Anomaly detection (evidence chat luong signal) |
| 7 | CP4 | `get_column_downstream` transitive that | Lineage + blast radius (15d) + bonus column lineage (+7) |
| 8 | CP4 | Verify `extract_dbt_dataset_graph` tren manifest that | Lineage advanced |
| 9 | CP5 | `evaluate_multiwindow_burn` policy Google SRE that | SLO (10d) + bonus multi-window (+7) |
| 10 | CP6 | KB freshness/SLO wiring (dong TODO co chu dich) | SLO (10d) + Mystery RCA (15d) |

**Trang thai cuoi cung (truoc hidden eval):** `make reset` -> `pytest tests_public -q` (19 passed) -> `make dbt` (PASS=21/TOTAL=21) -> `make baseline` (moi signal deu khoe, khong con false positive) deu xanh. Repo o trang thai khoe, san sang de instructor chay hidden evaluation.

## Decision 11 — Phan hoi ket qua hidden evaluation (17/20)

Ket qua giang vien tra ve: **17/20**, 3 case FAIL: `H09` (hard, anomaly), `H13` (hard, slo), `H18` (expert, rag). Khong co chi tiet assertion (dung nhu README noi: hidden test khong nam trong ZIP), chi co category + level, nen dieu tra dua tren bang chung con lai trong chinh starter code thay vi doan mo tuy tien (dung tinh than "khong nen build/sua mu quang" cua AI_AGENT_GUIDE).

**H18 (rag, expert) — Accept, confidence cao:** `detect_embedding_norm_shift` (`rag_embedding_shift` trong STUDENT_API) **chua bao gio duoc implement** qua ca 7 CP truoc — van con nguyen `return {"is_anomaly": False, "score": 0.0, "method": "not_implemented"}` tu starter. Day khong phai suy doan: ham nay chac chan luon tra `is_anomaly=False` bat ke input, nen bat ky hidden case nao ky vong `True` deu se fail. Fix: implement 2 tin hieu doc lap (moi cai du de flag) — (1) mean-shift qua `zscore_detector` tren gia tri norm trung binh so voi baseline (dung pattern giong `detect_text_length_shift` da co san); (2) dispersion-shift qua ty le std(current)/std(baseline) (bat truong hop mean binh thuong nhung mot so vector bi corrupt/zero-out). Verify: stable norms -> False; mean shift (norm ~3.5 so voi baseline ~1.0) -> True; dispersion shift (mean binh thuong nhung std tang 46 lan) -> True. Them 3 test moi vao tests_public/test_rag_metrics.py.

**H09 (anomaly, hard) — Accept, confidence trung binh-cao (co bang chung tai lieu, khong phai doan mu):** Docstring GOC cua `detect_anomaly` (truoc khi CP3 sua) liet ke ro cac context key giang vien co the dung de test: `day_of_week`, `same_segment_history`, `metric_name`, `known_event`, va **`trend`**. CP3 (Decision 5) da xu ly 4/5 key nay nhung **bo sot `trend` hoan toan** — day la manh moi ro rang nhat con lai, khong phai doan ngau nhien. Van de: mot metric dang tang deu (vd +20/ngay) se bi detector dua tren MUC (median/MAD cua gia tri tho) bao sai anomaly moi ngay, vi gia tri hom nay luon "xa" median cua lich su cu hon — du no dang tang dung theo trend da biet. Fix: them `_trend_residual_detector` — khi co `context["trend"]` (buoc thay doi ky vong moi ky, vd tang trung binh/ngay), so sanh **step residual** (`current - baseline[-1] - trend`) voi phan phoi residual cua chinh cac buoc lich su (`diff(history) - trend`), dung median/MAD cho robust. Metric tiep tuc dung trend -> residual ~0 -> khong anomaly; trend dao chieu dot ngot -> residual lon -> anomaly. Verify: tiep tuc trend +20/ngay (1120->1140) -> False (level-based cu se cho True/score=1.35, sai); dao chieu dot ngot (1120->850) -> True/score=inf. Them 2 test moi vao tests_public/test_anomaly.py.

**H13 (slo, hard) — Quyet dinh cuoi: GIU NGUYEN threshold hien tai, khong sua.** `evaluate_multiwindow_burn` da implement policy Google SRE that (14.4x/1h + 6x/6h cho page, verify boundary case dung). Khac voi H09/H18, khong co manh moi tai lieu ro rang nao trong starter chi ra con so/nguong cu the giang vien mong doi — chi co cau mo ta dinh tinh "sustained fast burn -> page, transient spike -> khong page" ma implementation hien tai da thoa man cho cac vi du cuc doan (20/10 -> page True; 20/0.5 -> page False). Da hoi nguoi dung xem cong cu cham co in chi tiet hon (input/expected/actual) khong -> **khong co**, chi co bang tom tat CASE/STATUS/LEVEL/CATEGORY. Trong tinh huong khong co them evidence, da dua ra 2 lua chon: (a) giu nguyen threshold co can cu ro rang (Google SRE Workbook), khong rui ro lam hong case SLO khac dang pass; (b) doi sang nguong tron/don gian hon (vd 2x ca 2 cua so) — la mot phong doan co ly nhung khong co bang chung truc tiep, co rui ro lam hong case SLO khac neu co nhieu hon 1 hidden test trong category `slo`. Nguoi dung chon **(a) giu nguyen** — uu tien an toan (khong risk 17->16 hoac thap hon) hon la thu doan co rui ro doi voi 1 diem chua chac chan. Rationale da duoc ghi lai day du de neu sau nay co them thong tin (vd giang vien chia se chi tiet hoac cho phep hoi), co the quay lai sua co can cu.

- Evidence tong hop: `pytest tests_public -q` -> 24 passed (them 5 test moi: 2 trend + 3 embedding). `make dbt`: 21/21. `make baseline`: van khoe, khong regressions.

## Decision 12 — H09 van FAIL sau vong 2 (18/20): "trend" sai huong, sua dung goc re

Ket qua vong 2: **18/20**. H18 da fix (confirmed). H13 van FAIL (dung du doan, khong sua vi khong co evidence). Nhung **H09 van FAIL** dù da them trend-awareness — chung to gia thuyet "trend" o Decision 11 sai (hoac khong du), can dieu tra lai.

- Hypothesis moi: Doc lai comment GOC (truoc CP3 sua) trong `scripts/run_baseline.py`: *"Public example: segment by weekday before applying the simple detector. Hidden evaluation still challenges students to make detect_metric(..., context=...) context-aware **instead of relying on caller-side preprocessing**."* Day la manh moi manh hon "trend" nhieu — no noi truc tiep hidden eval se KHONG tu pre-filter `same_segment_history` truoc khi goi, ma se truyen `history` THO (mixed weekday/weekend) + chi co `day_of_week` trong context, va ky vong chinh `auto` phai tu suy ra same-weekday baseline. Implementation CP3 (Decision 5/6) van yeu cau **caller** tu tinh san `same_segment_history` — chua bao gio tu suy luan tu `history` tho. Day moi la gap that.
- Prompt / request to agent: Them kha nang `_auto_baseline()` tu suy ra same-weekday segment truc tiep tu `history` tho khi chi co `context["day_of_week"]` (khong co `same_segment_history`), gia dinh `history` la chuoi ngay lien tuc ket thuc ngay truoc `current` (dung voi cau truc `metrics_history.csv`).
- Agent proposal: `_infer_same_weekday_segment(history, day_of_week)` — voi tung diem `history[-(k+1)]`, weekday = `(day_of_week - (k+1)) % 7`; giu lai diem co weekday == day_of_week. Can >=10 diem lich su va >=3 diem cung weekday moi dung (neu khong du, fallback ve raw_history nhu cu). Uu tien: `same_segment_history` (neu caller da truyen) > suy luan tu `day_of_week` > raw_history.
- Evidence/test: Dung du lieu mo phong dung cau truc that (21 ngay lien tuc, current=Thu Bay dow=5, KHONG truyen same_segment_history): gia tri Thu Bay hop le (260) -> `is_anomaly=False, baseline_source=inferred_same_weekday_from_history`; gia tri bat thuong (600, muc weekday tren 1 ngay Thu Bay) -> `is_anomaly=True`. Them test moi vao tests_public/test_anomaly.py voi jitter period-4 (co y, tranh trung voi chu ky weekday period-7 gay zero-spread gia tao — phat hien qua 1 lan chay test FAIL truoc do). `pytest tests_public -q`: 25 passed. `make dbt`: 21/21 khong doi. `make baseline`: van khoe.
- Accept / reject / revise: Accept.
- Why: Day la manh moi truc tiep tu CHINH starter code (khong phai suy doan tu tai lieu chung chung nhu Decision 11), do do confidence cao hon nhieu so voi gia thuyet "trend" ban dau. Giu nguyen trend-awareness (Decision 11) vi khong co bang chung no sai/thua, chi la KHONG DU — 2 co che nay bo sung cho nhau (uu tien same_segment_history > suy luan weekday > trend-residual (neu duoc goi rieng qua context) > MAD/zscore level-based).

**Luu y ve H13:** van chua co evidence moi, giu nguyen quyet dinh CP5 (threshold Google SRE, khong doi mu).
