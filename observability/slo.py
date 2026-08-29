from __future__ import annotations

from typing import Any


def calculate_slo(target: float, bad_events: int, total_events: int) -> dict[str, Any]:
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1 (exclusive)")
    if bad_events < 0 or total_events < 0 or bad_events > total_events:
        raise ValueError("invalid event counts")
    allowed_bad_rate = 1.0 - target
    if total_events == 0:
        return {
            "target": target,
            "actual_bad_rate": 0.0,
            "allowed_bad_rate": allowed_bad_rate,
            "burn_rate": 0.0,
            "remaining_error_budget_fraction": 1.0,
            "breached": False,
        }
    actual_bad_rate = bad_events / total_events
    burn_rate = actual_bad_rate / allowed_bad_rate
    consumed_fraction = min(1.0, actual_bad_rate / allowed_bad_rate)
    return {
        "target": target,
        "actual_bad_rate": actual_bad_rate,
        "allowed_bad_rate": allowed_bad_rate,
        "burn_rate": burn_rate,
        "remaining_error_budget_fraction": max(0.0, 1.0 - consumed_fraction),
        "breached": bool(actual_bad_rate > allowed_bad_rate),
    }


#  Google SRE Workbook style thresholds (https://sre.google/workbook/alerting-on-slos/):
#  a 14.4x burn rate exhausts a 30-day budget in ~1h if sustained; requiring
#  a shorter window to confirm the longer window is what tells a real incident
#  apart from a spike that self-resolves before eating meaningful budget.
FAST_BURN_SHORT_THRESHOLD = 14.4
FAST_BURN_LONG_THRESHOLD = 6.0
ELEVATED_LONG_BURN_THRESHOLD = 1.0


def evaluate_multiwindow_burn(
    *,
    short_window_burn: float,
    long_window_burn: float,
    policy: str = "default",
) -> dict[str, Any]:
    """Multi-window, multi-burn-rate SLO policy.

    A short-window spike alone must not page: it can resolve on its own
    before consuming a meaningful fraction of the larger window's error
    budget (a "transient spike"). Paging requires BOTH windows to show a
    fast burn at the same time -- that combination is what distinguishes a
    sustained, budget-threatening incident from noise. A long window that is
    elevated but not fast still surfaces as a non-paging warning, since slow
    steady budget consumption is real even when nothing looks urgent yet.
    """
    if short_window_burn >= FAST_BURN_SHORT_THRESHOLD and long_window_burn >= FAST_BURN_LONG_THRESHOLD:
        return {
            "page": True,
            "severity": "critical",
            "reason": (
                f"sustained fast burn: short_window_burn={short_window_burn} >= "
                f"{FAST_BURN_SHORT_THRESHOLD} and long_window_burn={long_window_burn} >= "
                f"{FAST_BURN_LONG_THRESHOLD}"
            ),
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
        }

    if short_window_burn >= FAST_BURN_SHORT_THRESHOLD:
        return {
            "page": False,
            "severity": "warning",
            "reason": (
                f"transient spike: short_window_burn={short_window_burn} >= "
                f"{FAST_BURN_SHORT_THRESHOLD} but long_window_burn={long_window_burn} < "
                f"{FAST_BURN_LONG_THRESHOLD} (not sustained, no page)"
            ),
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
        }

    if long_window_burn >= ELEVATED_LONG_BURN_THRESHOLD:
        return {
            "page": False,
            "severity": "warning",
            "reason": (
                f"slow sustained burn: long_window_burn={long_window_burn} >= "
                f"{ELEVATED_LONG_BURN_THRESHOLD} (ticket-worthy, not page-worthy)"
            ),
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
        }

    return {
        "page": False,
        "severity": "info",
        "reason": "burn rate within budget on both windows",
        "short_window_burn": short_window_burn,
        "long_window_burn": long_window_burn,
    }
