from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

from student_api import validate_orders

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "orders_contract.yaml"


def healthy_df():
    # created_at/updated_at are relative to "now" (not hardcoded literals) so
    # this fixture stays fresh under the freshness check regardless of when
    # the suite runs (contract: updated_at max_delay_minutes=30).
    now = datetime.now(timezone.utc)
    return pd.DataFrame([
        {
            "order_id": 1,
            "customer_id": "C1",
            "amount": 10.0,
            "currency": "USD",
            "status": "completed",
            "created_at": (now - timedelta(minutes=10)).isoformat(),
            "updated_at": (now - timedelta(minutes=5)).isoformat(),
        },
        {
            "order_id": 2,
            "customer_id": "C2",
            "amount": 20.0,
            "currency": "USD",
            "status": "pending",
            "created_at": (now - timedelta(minutes=9)).isoformat(),
            "updated_at": (now - timedelta(minutes=4)).isoformat(),
        },
    ])


def failed(issues):
    return [i for i in issues if not i["passed"]]


def test_healthy_contract_passes_starter_checks():
    assert not failed(validate_orders(healthy_df(), CONTRACT))


def test_duplicate_order_id_is_detected():
    df = healthy_df()
    df.loc[1, "order_id"] = 1
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "unique" and i["column"] == "order_id" for i in issues)


def test_invalid_currency_is_detected():
    df = healthy_df()
    df.loc[0, "currency"] = "BTC"
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "accepted_values" and i["column"] == "currency" for i in issues)


def test_type_drift_is_detected():
    df = healthy_df()
    df["amount"] = df["amount"].astype(object)
    df.loc[0, "amount"] = "not-a-number"
    issues = failed(validate_orders(df, CONTRACT))
    type_issue = next(i for i in issues if i["check"] == "type" and i["column"] == "amount")
    assert type_issue["severity"] == "critical"
    assert type_issue["action"] == "block"


def test_stale_updated_at_is_detected():
    df = healthy_df()
    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    df["updated_at"] = stale
    issues = failed(validate_orders(df, CONTRACT))
    freshness_issue = next(i for i in issues if i["check"] == "freshness")
    assert freshness_issue["column"] == "updated_at"
    assert freshness_issue["action"] == "quarantine"  # contract severity=warning
