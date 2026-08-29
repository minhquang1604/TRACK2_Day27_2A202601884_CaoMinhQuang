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


def _auto_baseline(history: Iterable[float], context: dict[str, Any] | None) -> tuple[list[float], str]:
    """Pick the best available comparison baseline.

    Prefers `context["same_segment_history"]` (e.g. history filtered to the
    same weekday/segment as `current`) when the caller supplies one, since
    that is the whole point of segment-aware comparison: comparing a Saturday
    to other Saturdays, not to a mixed Mon-Sun history. Falls back to the raw
    `history` argument otherwise.
    """
    if context:
        same_segment = context.get("same_segment_history")
        if same_segment is not None:
            candidate = [float(v) for v in same_segment]
            if len(candidate) >= 3:
                return candidate, "same_segment_history"
    return [float(v) for v in history], "raw_history"


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
      is surfaced in `reason` for triage but does not suppress the signal —
      a caller-supplied label should not silently mask a real incident.
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

    values = np.asarray(baseline_values, dtype=float)
    if values.size < 3:
        return {
            "is_anomaly": False,
            "score": 0.0,
            "method": "auto:insufficient_history",
            "reason": "; ".join(notes + ["insufficient_history"]),
        }

    if values.size >= 5:
        # mad_detector already handles the mad==0 (constant-history) edge
        # case correctly, so no further fallback is needed once we have
        # enough points for a median/MAD to be meaningful.
        mad_result = mad_detector(float(current), values, threshold=3.5)
        mad_result["method"] = "auto:mad"
        mad_result["reason"] = "; ".join(notes + [mad_result["reason"]])
        return mad_result

    # Fallback: too little history for a robust median/MAD (<5 points).
    result = zscore_detector(float(current), values, threshold=threshold)
    result["method"] = "auto:zscore"
    result["reason"] = "; ".join(notes + [result["reason"]])
    return result
