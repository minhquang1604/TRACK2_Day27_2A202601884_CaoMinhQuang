from student_api import detect_metric


def test_large_volume_drop_is_anomaly():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(300, history, method="zscore")
    assert result["is_anomaly"] is True


def test_stable_value_is_not_anomaly():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(1002, history, method="zscore")
    assert result["is_anomaly"] is False


def test_documented_positional_signature_works():
    # docs/STUDENT_API.md documents this as
    # detect_metric(current, history, method="auto", context=None), so a
    # caller following that signature positionally must not hit a TypeError.
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    assert detect_metric(300, history, "zscore")["is_anomaly"] is True
    assert detect_metric(300, history, "auto", {"metric_name": "row_count"})["is_anomaly"] is True


def test_known_event_suppresses_expected_deviation():
    # A caller announcing a known event (launch, promo, planned backfill) is
    # saying the deviation is expected -- it must not page, but the measured
    # score stays visible for auditing.
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(3000, history, "auto", {"metric_name": "row_count", "known_event": "black_friday"})
    assert result["is_anomaly"] is False
    assert result["suppressed_by_known_event"] is True
    assert result["score"] > 0

    # No known event -> the same value is still a genuine anomaly.
    assert detect_metric(3000, history, "auto", {"known_event": None})["is_anomaly"] is True


def test_legit_saturday_volume_is_not_flagged_against_weekday_history():
    # A same-segment (Saturday-only) baseline is much lower than a typical
    # weekday. `auto` must compare against the segment supplied via context,
    # not the raw weekday-scale `history` positional argument.
    weekday_history = [598, 605, 592, 610, 601, 597, 603, 599]
    saturday_history = [247, 262, 268, 235, 235, 258]
    result = detect_metric(
        250,
        weekday_history,
        method="auto",
        context={"metric_name": "row_count", "day_of_week": 5, "same_segment_history": saturday_history},
    )
    assert result["is_anomaly"] is False


def test_true_drop_within_same_segment_is_still_anomaly():
    saturday_history = [247, 262, 268, 235, 235, 258]
    result = detect_metric(
        60,
        saturday_history,
        method="auto",
        context={"metric_name": "row_count", "day_of_week": 5, "same_segment_history": saturday_history},
    )
    assert result["is_anomaly"] is True


def test_mad_zero_history_flags_any_deviation():
    # Regression for the starter's "mad_is_zero_todo" edge case: a perfectly
    # constant history has no spread, but that does not mean nothing can be
    # anomalous — any deviation from the constant is notable.
    result = detect_metric(10, [5, 5, 5, 5, 5], method="mad")
    assert result["is_anomaly"] is True
    assert detect_metric(5, [5, 5, 5, 5, 5], method="mad")["is_anomaly"] is False


def test_value_following_known_trend_is_not_anomaly():
    # A metric growing ~+20/day for a week is expected to keep growing by
    # ~+20 tomorrow too. A level-based check alone would flag this (1140 is
    # far from history's median), but context["trend"] tells `auto` to judge
    # the *step*, not the raw level.
    history = [1000, 1020, 1040, 1060, 1080, 1100, 1120]
    result = detect_metric(1140, history, method="auto", context={"metric_name": "row_count", "trend": 20})
    assert result["is_anomaly"] is False


def test_trend_reversal_is_still_anomaly():
    history = [1000, 1020, 1040, 1060, 1080, 1100, 1120]
    result = detect_metric(850, history, method="auto", context={"metric_name": "row_count", "trend": 20})
    assert result["is_anomaly"] is True


def test_auto_infers_same_weekday_segment_without_caller_prefiltering():
    # `history` is RAW and unsegmented (mixed weekday/weekend), and the
    # caller does NOT precompute `same_segment_history` -- only
    # `day_of_week` is given. `auto` must derive the same-weekday baseline
    # itself instead of requiring caller-side preprocessing (see
    # scripts/run_baseline.py's original starter comment on this exact
    # point). history is chronological (oldest first), ending the day
    # before `current`; `current` here is a Saturday (day_of_week=5).
    current_dow = 5
    n_days = 21
    weekday_scale, weekend_scale = 600, 258
    # Period-4 jitter (coprime with the period-7 weekday cycle) so each
    # weekday's points still get varied noise -- a period-7 jitter would
    # collide with the weekday cycle and produce a degenerate zero-spread
    # same-weekday segment.
    noise_cycle = [-6, 4, -2, 7]
    raw_history = []
    for i in range(n_days):
        days_before_current = n_days - i
        dow = (current_dow - days_before_current) % 7
        base = weekday_scale if dow < 5 else weekend_scale
        raw_history.append(base + noise_cycle[i % len(noise_cycle)])

    legit_saturday = detect_metric(
        260, raw_history, method="auto", context={"metric_name": "row_count", "day_of_week": current_dow}
    )
    assert legit_saturday["is_anomaly"] is False
    assert legit_saturday["reason"].startswith("baseline_source=inferred_same_weekday_from_history")

    anomalous_saturday = detect_metric(
        600, raw_history, method="auto", context={"metric_name": "row_count", "day_of_week": current_dow}
    )
    assert anomalous_saturday["is_anomaly"] is True
