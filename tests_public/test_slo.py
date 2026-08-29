import pytest
from student_api import multiwindow_burn, slo_status


def test_burn_rate_math():
    result = slo_status(0.995, bad_events=2, total_events=100)
    assert result["allowed_bad_rate"] == pytest.approx(0.005)
    assert result["actual_bad_rate"] == pytest.approx(0.02)
    assert result["burn_rate"] == pytest.approx(4.0)
    assert result["breached"] is True


def test_zero_events_is_safe():
    result = slo_status(0.99, bad_events=0, total_events=0)
    assert result["burn_rate"] == 0
    assert result["breached"] is False


def test_sustained_fast_burn_pages():
    # Both the short and long window are burning fast: a real, ongoing incident.
    result = multiwindow_burn(short_window_burn=20.0, long_window_burn=10.0)
    assert result["page"] is True
    assert result["severity"] == "critical"


def test_sustained_burn_between_page_tiers_still_pages():
    # 6x-14.4x on both windows is the SRE Workbook's second page tier (5% of
    # the budget in 6h). Requiring 14.4 on the short window while only asking
    # 6 on the long one would mix two tiers and silently never page here.
    result = multiwindow_burn(short_window_burn=10.0, long_window_burn=8.0)
    assert result["page"] is True


def test_transient_spike_does_not_page():
    # Short window spikes, but the long window is still healthy: it will
    # likely self-resolve before it eats a meaningful chunk of the budget.
    result = multiwindow_burn(short_window_burn=20.0, long_window_burn=0.5)
    assert result["page"] is False
