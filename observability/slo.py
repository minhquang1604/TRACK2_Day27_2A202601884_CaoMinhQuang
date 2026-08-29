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


#  Google SRE Workbook thresholds (https://sre.google/workbook/alerting-on-slos/).
#  Each alerting tier applies ONE burn rate to BOTH of its windows -- the long
#  window decides whether the burn matters, the short window confirms it is
#  still happening right now rather than already over:
#    page  : 14.4x  (2% of a 30-day budget in 1h;  windows 1h  / 5m)
#    page  :  6x    (5% in 6h;                     windows 6h  / 30m)
#    ticket:  1x    (10% in 3 days;                windows 3d  / 6h)
#  So anything at or above the 6x page tier on BOTH windows pages; pairing
#  14.4 on one window with 6 on the other would mix two different tiers and
#  silently never page a sustained 6-14x burn, which is itself a page-worthy
#  incident under this policy.
FAST_BURN_PAGE_THRESHOLD = 6.0
FAST_BURN_CRITICAL_THRESHOLD = 14.4
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
    if short_window_burn >= FAST_BURN_PAGE_THRESHOLD and long_window_burn >= FAST_BURN_PAGE_THRESHOLD:
        both_critical = (
            short_window_burn >= FAST_BURN_CRITICAL_THRESHOLD
            and long_window_burn >= FAST_BURN_CRITICAL_THRESHOLD
        )
        tier = FAST_BURN_CRITICAL_THRESHOLD if both_critical else FAST_BURN_PAGE_THRESHOLD
        return {
            "page": True,
            "severity": "critical",
            "reason": (
                f"sustained fast burn: short_window_burn={short_window_burn} and "
                f"long_window_burn={long_window_burn} both >= {tier} "
                f"(burning the error budget fast and still ongoing)"
            ),
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
            "burn_tier": tier,
        }

    if short_window_burn >= FAST_BURN_PAGE_THRESHOLD:
        return {
            "page": False,
            "severity": "warning",
            "reason": (
                f"transient spike: short_window_burn={short_window_burn} >= "
                f"{FAST_BURN_PAGE_THRESHOLD} but long_window_burn={long_window_burn} < "
                f"{FAST_BURN_PAGE_THRESHOLD} (not sustained, no page)"
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
