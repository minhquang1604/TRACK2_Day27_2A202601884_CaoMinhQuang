# Incident Report

## Severity
P2 — revenue-facing dashboard silently inflated/corrupted, but caught before publish (blocked, not shipped).

## Summary
`data/incoming/orders.csv` received 3 duplicate `order_id` rows from a non-idempotent
ingestion write (600 → 603 rows, 3 order_ids repeated). The row-count change was too
small (0.5%) for the anomaly detector to notice, but the `order_id` **uniqueness**
contract was violated — exactly the class of failure a data contract exists to catch
deterministically, independent of how "normal" the traffic volume looks. Reproduced
locally with `python scripts/inject_fault.py duplicate_pk`.

## Detection
- Signal: `src/contract_validator.py` → `check=unique, column=order_id, severity=critical,
  action=block` (via `validate_orders`), independently confirmed by the Great
  Expectations checkpoint (`expect_column_values_to_be_unique` → `action=block`), and by
  dbt's `unique_stg_orders_order_id` generic test (`FAIL 3`).
- First observed time: immediately after the faulty write — `updated_at` freshness stayed
  at ~5.0 minutes (fresh), which is itself a clue: this is a **data-quality** defect
  introduced in a single write, not a **staleness/delay** problem. Freshness alone would
  have missed it entirely.
- Signal that did **NOT** fire: `row_count_anomaly` (`auto:mad`) stayed `False`
  (600→603 is within normal noise). This is expected and important — it demonstrates why
  contract checks and anomaly detection are complementary, not redundant: anomaly
  detection catches *volume/shape* drift, contracts catch *structural* violations that
  can hide inside a perfectly normal-looking volume.

## Root Cause
Upstream order ingestion is not idempotent: a retried/duplicated write appended 3 rows
that already existed (same `order_id`), instead of upserting or de-duplicating before
landing in `data/incoming/orders.csv`.

## Evidence
1. `python scripts/run_baseline.py` after fault injection: `contract failed checks: 1`,
   `critical contract fails: 1` (`reports/latest_metrics.json`).
2. `python gx/validate_orders.py`: independent GX Suite/Checkpoint reaches the same
   conclusion — `expect_column_values_to_be_unique` fails with `severity=critical` →
   `action=block` (`reports/gx_validation_result.json`).
3. `make dbt`: `unique_stg_orders_order_id` → `FAIL 3` (exactly the 3 injected duplicate
   rows), and dbt's own DAG **automatically skips** `fct_daily_revenue` and all 8 tests/
   unit tests downstream of it rather than building on top of known-bad data.

## Blast Radius
```text
stg_orders (duplicate order_id rows)
  -> fct_daily_revenue          (would double-count 3 orders' revenue if built)
  -> ceo_revenue_dashboard      (CEO would see inflated daily revenue)
```
Computed via `downstream_assets(lineage_graph, "stg_orders")` →
`['fct_daily_revenue', 'ceo_revenue_dashboard']`. Confirmed in practice: `dbt build`
refused to materialize `fct_daily_revenue` once the upstream unique test failed
(9 downstream nodes `SKIP`ped), so the blast radius was contained at the transformation
layer and never reached the dashboard in this run.

## Mitigation
- Pipeline action from both contract validator and GX is `block` (severity=critical) —
  the correct response is to halt promotion of this batch, not warn-and-continue.
- Immediate fix: de-duplicate `orders.csv` on `order_id` (keep one row per id) before
  re-running `make dbt`.
- Root fix (upstream): make the ingestion write idempotent (upsert keyed on
  `order_id`, or dedupe-on-write) so a retry can never produce duplicate rows again.

## Recovery
```bash
make reset   # restores the healthy baseline snapshot
make baseline
make dbt
pytest tests_public -q
```

## Verification
- [x] Contract healthy — `python scripts/run_baseline.py` → `contract failed checks: 0`,
      `critical contract fails: 0`.
- [x] dbt tests healthy — `make dbt` → `PASS=21 WARN=0 ERROR=0 SKIP=0 NO-OP=0 TOTAL=21`.
- [x] Anomaly returned to expected range — `row-count anomaly: False (auto:mad, score=0.76)`.
- [x] SLO healthy / budget understood — contract SLO (`target=0.999`) back to
      `breached=False`; KB freshness SLO (`target=0.99`) `breached=False,
      stale_docs=0/5` (see `reports/agent_log.md` Decision 10 for the related
      `stale_kb` gap this lab also closed).
- [x] Downstream output verified — `make dbt` rebuilds `fct_daily_revenue` and all
      21 tests/unit tests pass with no `SKIP`.

## Prevention / Action Items
| Action | Owner | Deadline | Why |
|---|---|---|---|
| Make order ingestion write idempotent (upsert on `order_id`) | Commerce-data ingestion team | +1 week | Root cause of this incident; contract/GX/dbt only detect the symptom after the fact |
| Keep all 3 independent `unique` checks (contract, GX, dbt) rather than consolidating to one | Data/AI Reliability team | Ongoing | This incident's anomaly detector did *not* fire (603 vs 600 is normal noise) — only the structural uniqueness checks caught it; defense-in-depth across layers is what actually worked here |
| Wire KB freshness into `run_baseline.py`'s SLO reporting (`rag_index_freshness`) | Data/AI Reliability team | Done, this cycle | Was a silent gap — `stale_kb` fault previously produced no signal anywhere in the pipeline (see `reports/agent_log.md` Decision 10) |
| Tag block-severity failures distinctly in on-call alerting (not just "test FAIL") | Data/AI Reliability team | +2 weeks | `action=block` should short-circuit an on-call decision, not require re-deriving severity from a wall of test output |
