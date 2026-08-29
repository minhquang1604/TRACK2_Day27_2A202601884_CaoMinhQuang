"""Anomaly detection.

`zscore_detector` is kept as-is (simple, well-understood baseline). `auto`
mode is context-aware: it prefers a same-segment baseline (e.g. same weekday)
when the caller provides one, and uses a robust median/MAD statistic instead
of mean/std whenever there is enough history, falling back to z-score for
short or degenerate baselines.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def zscore_detector(current: float, history: Iterable[float], threshold: float = 3.0) -> dict[str, Any]:
    values = np.asarray(list(history), dtype=float)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "zscore", "reason": "insufficient_history"}
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        score = float("inf") if float(current) != mean else 0.0
    else:
        score = abs(float(current) - mean) / std
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "zscore",
        "reason": f"mean={mean:.3f}, std={std:.3f}, threshold={threshold}",
    }


def mad_detector(current: float, history: Iterable[float], threshold: float = 3.5) -> dict[str, Any]:
    """Robust median/MAD detector.

    Resistant to the occasional outlier sitting inside the history window
    (a promo spike, a partial outage day) in a way mean/std is not, since
    median and MAD are themselves computed from order statistics rather than
    from every value equally.
    """
    values = np.asarray(list(history), dtype=float)
    if values.size < 5:
        return {"is_anomaly": False, "score": 0.0, "method": "mad", "reason": "insufficient_history"}
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad == 0:
        # Every history point is identical: there is no spread to normalize
        # by, but that does not mean "no anomaly is possible" — it means any
        # deviation at all from a perfectly constant baseline is notable.
        if float(current) == median:
            return {
                "is_anomaly": False,
                "score": 0.0,
                "method": "mad",
                "reason": f"mad_is_zero, current equals constant median={median:.3f}",
            }
        return {
            "is_anomaly": True,
            "score": float("inf"),
            "method": "mad",
            "reason": f"mad_is_zero, current={current} differs from constant median={median:.3f}",
        }
    modified_z = 0.6745 * abs(float(current) - median) / mad
    return {
        "is_anomaly": bool(modified_z > threshold),
        "score": float(modified_z),
        "method": "mad",
        "reason": f"median={median:.3f}, mad={mad:.3f}, threshold={threshold}",
    }


def _trend_residual_detector(
    current: float, values: np.ndarray, expected_step: float, threshold: float = 3.5
) -> dict[str, Any] | None:
    """Compare the observed step (current - last baseline point) against the
    history's own day-over-day steps, after removing an externally supplied
    `expected_step` (context["trend"]) from both.

    A metric that is genuinely trending (steadily growing/shrinking) will
    keep failing a level-based median/MAD check forever, since "today" is
    expected to differ from the bulk of history by design. Comparing *step*
    residuals instead means a value that continues the known trend looks
    normal, while a sudden acceleration, reversal, or flattening — a real
    change in trend — still stands out. Returns None when there is not
    enough history (<3 historical steps) for a robust residual baseline.
    """
    diffs = np.diff(values)
    if diffs.size < 3:
        return None
    residuals = diffs - expected_step
    median_r = float(np.median(residuals))
    mad_r = float(np.median(np.abs(residuals - median_r)))
    actual_step = float(current) - float(values[-1])
    actual_residual = actual_step - expected_step

    if mad_r == 0:
        is_anomaly = actual_residual != median_r
        score = float("inf") if is_anomaly else 0.0
    else:
        score = 0.6745 * abs(actual_residual - median_r) / mad_r
        is_anomaly = score > threshold

    return {
        "is_anomaly": bool(is_anomaly),
        "score": float(score),
        "method": "auto:trend",
        "reason": (
            f"expected_step(trend)={expected_step}, actual_step={actual_step:.3f}, "
            f"residual={actual_residual:.3f}, median_residual={median_r:.3f}, mad_residual={mad_r:.3f}"
        ),
    }


def _infer_same_weekday_segment(history: list[float], day_of_week: int) -> list[float] | None:
    """Derive a same-weekday baseline directly from a raw, unsegmented daily
    history series, when the caller did not pre-filter one.

    Assumes `history` is a consecutive daily time series ending the day
    before `current` (true for `data/history/metrics_history.csv` and for
    any day-over-day metric log) -- so `history[-1]` is 1 day before
    `current`, `history[-2]` is 2 days before, and so on. That lets us work
    out each entry's weekday relative to `current`'s (`day_of_week`) without
    needing per-point weekday metadata, and keep only the entries that share
    `current`'s weekday. Returns None when there isn't enough history
    (< 3 same-weekday points) for this to be worth it.
    """
    n = len(history)
    if n < 10:
        return None
    segment = [
        value
        for k, value in enumerate(reversed(history))  # k=0 -> history[-1] (yesterday)
        if (day_of_week - (k + 1)) % 7 == day_of_week % 7
    ]
    if len(segment) < 3:
        return None
    segment.reverse()  # restore chronological (oldest-first) order
    return segment


def _auto_baseline(history: Iterable[float], context: dict[str, Any] | None) -> tuple[list[float], str]:
    """Pick the best available comparison baseline.

    1. `context["same_segment_history"]` (e.g. history filtered to the same
       weekday/segment as `current`) when the caller supplies one directly.
    2. Otherwise, if `context["day_of_week"]` is given, infer the
       same-weekday segment from the raw `history` series ourselves (see
       `_infer_same_weekday_segment`) -- `auto` should not require the
       caller to have already done the segmentation.
    3. Otherwise, fall back to the raw `history` argument as-is.

    Either way, this is the whole point of segment-aware comparison:
    comparing a Saturday to other Saturdays, not to a mixed Mon-Sun history.
    """
    history_list = [float(v) for v in history]
    if context:
        same_segment = context.get("same_segment_history")
        if same_segment is not None:
            candidate = [float(v) for v in same_segment]
            if len(candidate) >= 3:
                return candidate, "same_segment_history"

        day_of_week = context.get("day_of_week")
        if day_of_week is not None:
            inferred = _infer_same_weekday_segment(history_list, int(day_of_week))
            if inferred is not None:
                return inferred, "inferred_same_weekday_from_history"

    return history_list, "raw_history"


def detect_anomaly(
    current: float,
    history: Iterable[float],
    *,
    method: str = "auto",
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable lab API.

    - `zscore`: basic z-score (unchanged, see `zscore_detector`).
    - `mad`: median/MAD detector (see `mad_detector`).
    - `auto`: context-aware. Uses `context["same_segment_history"]` as the
      baseline when provided (falls back to `history` otherwise), then prefers
      a robust median/MAD statistic over mean/std whenever there is enough
      history (>=5 points), and falls back to z-score for short or
      degenerate (MAD==0, non-constant) baselines. `context["known_event"]`
      (e.g. a launch, a promo, a planned backfill) suppresses the alert the
      way a maintenance window does in any monitoring system: the deviation
      was expected, so it must not page. The measured `score` is still
      returned and `suppressed_by_known_event` records what happened, so the
      signal stays auditable instead of vanishing.
      `context["trend"]` (an expected step-over-step change, e.g. average
      day-over-day growth) switches to a step-residual comparison instead of
      a level comparison, so a metric that keeps following its known trend
      is not flagged just for being far from history's raw level.
    """
    if method == "mad":
        return mad_detector(current, history)
    if method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if method != "auto":
        raise ValueError(f"Unsupported method: {method}")

    context = context or {}
    baseline_values, baseline_source = _auto_baseline(history, context)
    notes = [f"baseline_source={baseline_source}"]
    known_event = context.get("known_event")
    if known_event:
        notes.append(f"known_event={known_event}")

    def finalize(result: dict[str, Any], extra_notes: list[str] | None = None) -> dict[str, Any]:
        result["reason"] = "; ".join(notes + (extra_notes or []) + [result.get("reason", "")]).rstrip("; ")
        if known_event:
            # Expected deviation announced by the caller: suppress the alert
            # (like a maintenance window) but keep the measured score visible.
            result["suppressed_by_known_event"] = True
            if result.get("is_anomaly"):
                result["is_anomaly"] = False
                result["reason"] += f"; suppressed: deviation expected during known_event={known_event}"
        return result

    values = np.asarray(baseline_values, dtype=float)
    if values.size < 3:
        return finalize(
            {
                "is_anomaly": False,
                "score": 0.0,
                "method": "auto:insufficient_history",
                "reason": "insufficient_history",
            }
        )

    trend = context.get("trend")
    if trend is not None:
        try:
            expected_step = float(trend)
        except (TypeError, ValueError):
            expected_step = None
        if expected_step is not None:
            trend_result = _trend_residual_detector(float(current), values, expected_step)
            if trend_result is not None:
                return finalize(trend_result)

    if values.size >= 5:
        # mad_detector already handles the mad==0 (constant-history) edge
        # case correctly, so no further fallback is needed once we have
        # enough points for a median/MAD to be meaningful.
        mad_result = mad_detector(float(current), values, threshold=3.5)
        mad_result["method"] = "auto:mad"
        return finalize(mad_result)

    # Fallback: too little history for a robust median/MAD (<5 points).
    result = zscore_detector(float(current), values, threshold=threshold)
    result["method"] = "auto:zscore"
    return finalize(result)
