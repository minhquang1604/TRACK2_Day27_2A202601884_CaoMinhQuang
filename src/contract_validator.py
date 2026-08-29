"""Contract validator used as the lab's deterministic validation layer.

Covers common deterministic checks plus:
- type validation (no more silent `errors="coerce"` swallowing of type drift),
- dataset-level freshness checks,
- severity-aware actions (block/quarantine/warn).

Students may still extend it with cross-field/cross-table assertions and
richer observability metadata.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

# Severity -> pipeline action policy. Kept in sync with gx/validate_orders.py
# so both validation layers agree on what a failure should actually do.
ACTION_BY_SEVERITY = {"critical": "block", "warning": "quarantine", "info": "warn"}
_ACTION_PRIORITY = {"none": 0, "warn": 1, "quarantine": 2, "block": 3}


def _action_for(severity: str, passed: bool) -> str:
    if passed:
        return "none"
    return ACTION_BY_SEVERITY.get(severity, "warn")


def _issue(
    check: str,
    *,
    column: str | None,
    severity: str,
    passed: bool,
    details: str,
) -> dict[str, Any]:
    return {
        "check": check,
        "column": column,
        "severity": severity,
        "passed": bool(passed),
        "action": _action_for(severity, passed),
        "details": details,
    }


def _type_invalid_mask(series: pd.Series, declared_type: str) -> pd.Series:
    """Return a boolean mask of non-null values that do not match `declared_type`.

    Unlike a bare `pd.to_numeric(series, errors="coerce")`, this makes type
    drift observable instead of silently turning bad values into NaN.
    """
    present = series.notna()
    declared_type = (declared_type or "").lower()

    if declared_type in {"integer", "int"}:
        coerced = pd.to_numeric(series, errors="coerce")
        non_integer = coerced.notna() & (coerced % 1 != 0)
        return present & (coerced.isna() | non_integer)

    if declared_type in {"number", "float", "numeric"}:
        coerced = pd.to_numeric(series, errors="coerce")
        return present & coerced.isna()

    if declared_type == "datetime":
        coerced = pd.to_datetime(series, errors="coerce", utc=True)
        return present & coerced.isna()

    if declared_type == "string":
        # A whole column silently read back as numeric/boolean (e.g. an ID
        # column losing leading zeros) is a real type-drift symptom even
        # though every individual value "parses".
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            return present
        return pd.Series(False, index=series.index)

    # Unknown/unspecified declared type: nothing to check.
    return pd.Series(False, index=series.index)


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_dataframe(df: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    columns = contract.get("columns", {})

    for column, rules in columns.items():
        severity = rules.get("severity", "warning")
        required = bool(rules.get("required", False))

        if column not in df.columns:
            if required:
                issues.append(
                    _issue(
                        "required_column",
                        column=column,
                        severity=severity,
                        passed=False,
                        details=f"Missing required column: {column}",
                    )
                )
            continue

        series = df[column]

        if required:
            null_count = int(series.isna().sum())
            issues.append(
                _issue(
                    "not_null",
                    column=column,
                    severity=severity,
                    passed=(null_count == 0),
                    details=f"null_count={null_count}",
                )
            )

        if rules.get("unique"):
            duplicate_count = int(series.duplicated(keep=False).sum())
            issues.append(
                _issue(
                    "unique",
                    column=column,
                    severity=severity,
                    passed=(duplicate_count == 0),
                    details=f"duplicate_rows={duplicate_count}",
                )
            )

        accepted = rules.get("accepted_values")
        if accepted is not None:
            invalid_mask = series.notna() & ~series.isin(accepted)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "accepted_values",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}; accepted={accepted}",
                )
            )

        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            invalid = pd.Series(False, index=series.index)
            if "min" in rules:
                invalid |= numeric < rules["min"]
            if "max" in rules:
                invalid |= numeric > rules["max"]
            invalid_count = int(invalid.fillna(False).sum())
            issues.append(
                _issue(
                    "range",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}",
                )
            )

        declared_type = rules.get("type")
        if declared_type:
            invalid_mask = _type_invalid_mask(series, declared_type)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "type",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"declared_type={declared_type}, invalid_count={invalid_count}",
                )
            )

    issues.extend(_validate_freshness(df, contract))

    return issues


def _validate_freshness(df: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    freshness = contract.get("freshness")
    if not freshness:
        return []

    column = freshness.get("column")
    max_delay_minutes = freshness.get("max_delay_minutes")
    severity = freshness.get("severity", "warning")

    if column not in df.columns:
        return [
            _issue(
                "freshness",
                column=column,
                severity=severity,
                passed=False,
                details=f"freshness column missing: {column}",
            )
        ]

    parsed = pd.to_datetime(df[column], errors="coerce", utc=True)
    valid = parsed.dropna()
    if valid.empty:
        return [
            _issue(
                "freshness",
                column=column,
                severity=severity,
                passed=False,
                details=f"no parseable timestamps in column: {column}",
            )
        ]

    now = pd.Timestamp.now(tz="UTC")
    delay_minutes = (now - valid.max()).total_seconds() / 60.0
    passed = max_delay_minutes is None or delay_minutes <= max_delay_minutes
    return [
        _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=passed,
            details=f"delay_minutes={delay_minutes:.2f}, max_delay_minutes={max_delay_minutes}",
        )
    ]


def overall_action(issues: list[dict[str, Any]]) -> str:
    """Worst-case pipeline action across all issues: block > quarantine > warn > none."""
    worst = "none"
    for issue in issues:
        action = issue.get("action", "none")
        if _ACTION_PRIORITY.get(action, 0) > _ACTION_PRIORITY.get(worst, 0):
            worst = action
    return worst


def failed_issues(issues: list[dict[str, Any]], min_severity: str | None = None) -> list[dict[str, Any]]:
    failed = [i for i in issues if not i.get("passed", False)]
    if min_severity is None:
        return failed
    order = {"info": 0, "warning": 1, "critical": 2}
    threshold = order[min_severity]
    return [i for i in failed if order.get(i.get("severity", "warning"), 1) >= threshold]
