#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.anomaly import detect_anomaly
from observability.lineage import get_downstream_assets
from observability.rag_metrics import detect_text_length_shift
from observability.slo import calculate_slo
from src.contract_validator import failed_issues, load_contract, validate_dataframe
from src.io_utils import load_jsonl, load_yaml


def main() -> None:
    orders = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    history = pd.read_csv(ROOT / "data" / "history" / "metrics_history.csv")
    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")
    issues = validate_dataframe(orders, contract)
    failed = failed_issues(issues)
    critical_failed = failed_issues(issues, min_severity="critical")

    # Segment history by weekday CLASS (Mon-Fri vs Sat-Sun), not by literally
    # matching today's real calendar weekday. `scripts/generate_data.py`
    # always generates "today's" order batch at full business-day volume
    # (no weekend scale-down is applied to data/incoming/orders.csv), so the
    # correct comparison group is the weekday segment of history regardless
    # of which real-world day this script happens to run on. Matching the
    # literal real weekday instead produced a false anomaly on a perfectly
    # healthy baseline whenever the lab was run on a real Saturday/Sunday
    # (see reports/agent_log.md, CP0 finding).
    current_dow = datetime.now().weekday()
    weekday_mask = history["day_of_week"] < 5
    segment = history.loc[weekday_mask, "row_count"].tail(10).tolist()
    row_history = segment if len(segment) >= 3 else history["row_count"].tail(14).tolist()
    row_result = detect_anomaly(
        len(orders),
        row_history,
        method="auto",
        context={"metric_name": "row_count", "day_of_week": current_dow, "same_segment_history": row_history},
    )

    updated = pd.to_datetime(orders["updated_at"], utc=True, errors="coerce")
    freshness_minutes = (
        pd.Timestamp(datetime.now(timezone.utc)) - updated.max()
    ).total_seconds() / 60.0

    docs = load_jsonl(ROOT / "data" / "incoming" / "kb_documents.jsonl")
    text_result = detect_text_length_shift(
        [d["content"] for d in docs], history["mean_text_length"].tail(14).tolist()
    )

    # KB freshness/SLO (previously a deliberate TODO: nothing in the pipeline
    # noticed a stale KB, since `text_result` only looks at content length,
    # not publish recency). Reuses contracts/kb_contract.yaml's own
    # freshness rule and lab_config.yaml's rag_index_freshness SLO target so
    # a stale RAG index shows up the same way any other SLO breach does.
    lab_config = load_yaml(ROOT / "lab_config.yaml")
    kb_contract = load_contract(ROOT / "contracts" / "kb_contract.yaml")
    kb_freshness_cfg = kb_contract.get("freshness", {})
    kb_column = kb_freshness_cfg.get("column", "published_at")
    kb_max_delay = kb_freshness_cfg.get("max_delay_minutes", 60)
    kb_published = pd.to_datetime([d.get(kb_column) for d in docs], utc=True, errors="coerce")
    now_ts = pd.Timestamp(datetime.now(timezone.utc))
    kb_delay_minutes = (now_ts - kb_published).total_seconds() / 60.0
    kb_stale_count = int((kb_delay_minutes > kb_max_delay).sum())
    kb_freshness_slo = calculate_slo(
        lab_config["slo"]["rag_index_freshness"]["target"],
        bad_events=kb_stale_count,
        total_events=len(docs),
    )

    # Demo SLO: one check event for this run.
    bad = 1 if critical_failed else 0
    contract_slo = calculate_slo(0.999, bad_events=bad, total_events=1)

    with open(ROOT / "data" / "baseline" / "lineage_graph.json", "r", encoding="utf-8") as f:
        lineage = json.load(f)["dataset_lineage"]
    blast_radius = get_downstream_assets(lineage, "stg_orders")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orders_rows": int(len(orders)),
        "failed_contract_checks": len(failed),
        "critical_contract_failures": len(critical_failed),
        "row_count_anomaly": row_result,
        "freshness_minutes": freshness_minutes,
        "kb_text_length_signal": text_result,
        "kb_freshness_minutes_max": float(kb_delay_minutes.max()),
        "kb_stale_doc_count": kb_stale_count,
        "kb_freshness_slo": kb_freshness_slo,
        "contract_slo": contract_slo,
        "sample_blast_radius_from_stg_orders": blast_radius,
    }
    out = ROOT / "reports" / "latest_metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=== DATA RELIABILITY BASELINE ===")
    print(f"orders rows              : {len(orders)}")
    print(f"contract failed checks   : {len(failed)}")
    print(f"critical contract fails  : {len(critical_failed)}")
    print(f"row-count anomaly        : {row_result['is_anomaly']} ({row_result['method']}, score={row_result['score']:.2f})")
    print(f"freshness minutes        : {freshness_minutes:.1f}")
    print(f"KB length anomaly        : {text_result['is_anomaly']}")
    print(f"KB freshness (max delay) : {kb_delay_minutes.max():.1f} min (max_delay={kb_max_delay}, stale_docs={kb_stale_count}/{len(docs)})")
    print(f"KB freshness SLO breach  : {kb_freshness_slo['breached']} (burn_rate={kb_freshness_slo['burn_rate']:.2f})")
    print(f"sample blast radius      : {', '.join(blast_radius)}")
    print(f"report                    : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
