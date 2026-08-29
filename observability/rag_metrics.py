from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from observability.anomaly import zscore_detector


def approximate_token_lengths(texts: Iterable[str]) -> list[int]:
    # Deliberately simple proxy; no tokenizer/model download needed.
    return [len(str(t).split()) for t in texts]


def detect_text_length_shift(
    current_texts: Iterable[str],
    baseline_batch_means: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    lengths = approximate_token_lengths(current_texts)
    current_mean = float(np.mean(lengths)) if lengths else 0.0
    result = zscore_detector(current_mean, baseline_batch_means, threshold=threshold)
    result["metric"] = "mean_text_length"
    result["current_mean"] = current_mean
    return result


def detect_embedding_norm_shift(
    current_norms: Iterable[float],
    baseline_norms: Iterable[float],
    *,
    threshold: float = 3.0,
    dispersion_ratio_threshold: float = 3.0,
) -> dict[str, Any]:
    """Detect embedding-space drift from a shift in embedding vector norms.

    No embedding model is required: healthy embeddings from a stable
    model/pipeline have a fairly consistent norm distribution (e.g. hovering
    around 1.0 for normalized vectors), so this looks for two independent
    proxies for drift, either of which is enough to flag an anomaly:
    - a shift in *mean* norm (e.g. a different embedding model/version, or
      systematically truncated/scaled vectors) via z-score against the
      baseline population;
    - a large change in norm *dispersion* (e.g. a batch where some vectors
      were silently zeroed out or corrupted) via the ratio of current to
      baseline standard deviation.
    """
    current = np.asarray(list(current_norms), dtype=float)
    baseline = np.asarray(list(baseline_norms), dtype=float)
    if current.size == 0 or baseline.size == 0:
        return {"is_anomaly": False, "score": 0.0, "method": "embedding_norm_shift", "reason": "empty_input"}

    current_mean = float(np.mean(current))
    mean_shift = zscore_detector(current_mean, baseline, threshold=threshold)

    baseline_std = float(np.std(baseline))
    current_std = float(np.std(current))
    if baseline_std == 0:
        dispersion_ratio = float("inf") if current_std != 0 else 1.0
    else:
        dispersion_ratio = current_std / baseline_std
    dispersion_anomaly = bool(
        dispersion_ratio >= dispersion_ratio_threshold
        or (dispersion_ratio > 0 and (1.0 / dispersion_ratio) >= dispersion_ratio_threshold)
    )

    return {
        "is_anomaly": bool(mean_shift["is_anomaly"] or dispersion_anomaly),
        "score": float(mean_shift["score"]),
        "method": "embedding_norm_shift",
        "reason": (
            f"mean_shift: {mean_shift['reason']}; current_mean={current_mean:.4f}; "
            f"dispersion_ratio(current_std/baseline_std)={dispersion_ratio:.3f}"
        ),
        "metric": "mean_embedding_norm",
        "current_mean": current_mean,
        "dispersion_anomaly": dispersion_anomaly,
    }
