from student_api import detect_metric


def test_large_volume_drop_is_anomaly():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(300, history, method="zscore")
    assert result["is_anomaly"] is True


def test_stable_value_is_not_anomaly():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(1002, history, method="zscore")
    assert result["is_anomaly"] is False


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
