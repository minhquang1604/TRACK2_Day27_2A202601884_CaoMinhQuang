#!/usr/bin/env python3
"""Great Expectations Core 1.21 validation flow for the orders dataset.

Upgraded from the original single-batch demo into a reusable
Expectation Suite + ValidationDefinition + Checkpoint + custom Action,
with severity-aware pipeline actions (block / quarantine / warn) mirroring
`src/contract_validator.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import great_expectations as gx
    from great_expectations.checkpoint import ActionContext, ValidationAction
    from great_expectations.checkpoint.checkpoint import CheckpointResult
except ImportError as exc:  # friendlier classroom failure
    raise SystemExit("great_expectations is not installed. Run: pip install -r requirements.txt") from exc

# Same severity -> action policy used by src/contract_validator.py, kept in
# sync intentionally so both validation layers agree on pipeline behavior.
ACTION_BY_SEVERITY = {"critical": "block", "warning": "quarantine", "info": "warn"}
_ACTION_PRIORITY = {"none": 0, "warn": 1, "quarantine": 2, "block": 3}


def _expectation_severity(expectation_config: Any) -> str:
    severity = getattr(expectation_config, "severity", "warning")
    return getattr(severity, "value", str(severity)).lower()


def summarize_severity_actions(checkpoint_result: "CheckpointResult") -> dict[str, Any]:
    """Turn a CheckpointResult into a block/quarantine/warn pipeline decision.

    This is the "Actions based on severity" piece requested by the lab guide:
    GX's own pass/fail is not enough to decide whether to stop the pipeline,
    quarantine the batch, or just warn — that mapping lives here.
    """
    failures: list[dict[str, Any]] = []
    overall_action = "none"
    for result in checkpoint_result.run_results.values():
        for expectation_result in result.results:
            if expectation_result.success:
                continue
            severity = _expectation_severity(expectation_result.expectation_config)
            action = ACTION_BY_SEVERITY.get(severity, "warn")
            failures.append(
                {
                    "expectation": expectation_result.expectation_config.type,
                    "column": expectation_result.expectation_config.kwargs.get("column"),
                    "severity": severity,
                    "action": action,
                }
            )
            if _ACTION_PRIORITY[action] > _ACTION_PRIORITY[overall_action]:
                overall_action = action
    return {"overall_action": overall_action, "failures": failures}


class SeverityAction(ValidationAction):
    """Custom GX Action: print the severity->action decision for each failure."""

    type: Literal["severity_action"] = "severity_action"

    def run(self, checkpoint_result: "CheckpointResult", action_context: ActionContext | None = None) -> dict:
        summary = summarize_severity_actions(checkpoint_result)
        for failure in summary["failures"]:
            print(
                f"  [{failure['severity']:<8}] {failure['expectation']:<40} "
                f"column={failure['column']} -> action={failure['action']}"
            )
        return summary


def build_expectation_suite() -> "gx.ExpectationSuite":
    return gx.ExpectationSuite(
        name="orders_suite",
        expectations=[
            gx.expectations.ExpectColumnValuesToNotBeNull(column="order_id", severity="critical"),
            gx.expectations.ExpectColumnValuesToBeUnique(column="order_id", severity="critical"),
            gx.expectations.ExpectColumnValuesToBeBetween(column="amount", min_value=0, severity="critical"),
            gx.expectations.ExpectColumnValuesToBeInSet(
                column="currency", value_set=["USD", "VND"], severity="critical"
            ),
            gx.expectations.ExpectColumnValuesToBeInSet(
                column="status",
                value_set=["pending", "completed", "refunded", "cancelled"],
                severity="warning",
            ),
        ],
    )


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    context = gx.get_context()

    # Use unique names so re-running inside an ephemeral context is simple.
    data_source = context.data_sources.add_pandas("orders_pandas")
    asset = data_source.add_dataframe_asset(name="orders_dataframe")
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_orders")

    suite = build_expectation_suite()
    context.suites.add(suite)

    validation_definition = gx.ValidationDefinition(
        name="orders_validation_definition", data=batch_definition, suite=suite
    )
    context.validation_definitions.add(validation_definition)

    checkpoint = gx.Checkpoint(
        name="orders_checkpoint",
        validation_definitions=[validation_definition],
        actions=[SeverityAction(name="severity_action")],
    )
    context.checkpoints.add(checkpoint)

    print("Running orders_checkpoint (Suite + ValidationDefinition + Checkpoint + Actions)...")
    result = checkpoint.run(batch_parameters={"dataframe": df})

    summary = summarize_severity_actions(result)
    out = ROOT / "reports" / "gx_validation_result.json"
    out.write_text(
        json.dumps(
            {"success": bool(result.success), **summary},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(f"\nGX checkpoint success: {result.success}")
    print(f"Pipeline action       : {summary['overall_action'].upper()}")
    print(f"Report                 : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
