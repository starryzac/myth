"""One real default-seed insufficient-history candidate; root runs it after wiring.

This does not fabricate two historical festivals or claim successful adoption.
"""

import pytest
from app.domain.policy_configuration import configuration_hash
from app.services.full_policy_lifecycle import canonical_candidate
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_actual_short_seed_seasonal_adoption_remains_advice_only_and_cannot_confirm(
    annual_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = annual_client
    secret = "SYNTHETIC_SEASONAL_LOCAL_SESSION_RISK_ONLY"
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", secret)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "ae" * 32)
    config = {
        "type": "seasonal_reserve",
        "name": "真实短历史不足候选",
        "holiday_code": "NATIONAL_DAY",
        "window": {"start": "2026-10-04", "end": "2026-10-07"},
        "lookback_days": 1096,
        "minimum_historical_windows": 2,
        "quantile": 0.8,
        "essential_categories": ["food", "transport", "daily_necessities"],
        "adjustment_cap_cents": 500000,
        "requires_confirmation": True,
        "advice_only": True,
    }
    created = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "SeasonalReservePolicy",
            "configuration": config,
            "reviewed_hash": configuration_hash(
                canonical_candidate("SeasonalReservePolicy", config)
            ),
            "accepted": True,
            "reason": "仅明确声明，不能用短历史补造金额",
            "idempotency_key": "seasonal-short-history-policy",
        },
    )
    assert created.status_code == 200, created.text
    original = created.json()
    policy, version, epoch = original["policy_id"], original["version_id"], original["epoch_id"]
    before = physical_snapshot(engine)
    preview_body = {"expected_version_id": version, "window_id": "CN-2026-NATIONAL_DAY"}
    preview = client.post(f"/api/v1/seasonal-reserve-adoptions/{policy}/preview", json=preview_body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "UNKNOWN" and preview.json()["scope"] is None
    assert preview.json()["reviewed_hash"] is None
    current = client.get(f"/api/v1/seasonal-reserve-adoptions/{policy}")
    assert current.status_code == 200, current.text
    assert current.json()["status"] == "ADVICE_ONLY" and current.json()["original"] is None
    assert current.json()["actual_adoption_count"] == 0
    confirm_body = preview_body | {
        "expected_epoch_id": epoch,
        "reviewed_hash": "0" * 64,
        "accepted": True,
        "reason": "缺原历史不能确认",
        "idempotency_key": "seasonal-short-history-attempt",
    }
    assert (
        client.post(
            f"/api/v1/seasonal-reserve-adoptions/{policy}/confirm", json=confirm_body
        ).status_code
        == 401
    )
    logged = client.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
    )
    assert logged.status_code == 200, logged.text
    refused = client.post(f"/api/v1/seasonal-reserve-adoptions/{policy}/confirm", json=confirm_body)
    assert refused.status_code == 409, refused.text
    lookup = client.get(
        f"/api/v1/seasonal-reserve-adoptions/commands/{epoch}/by-key/seasonal-short-history-attempt"
    )
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["status"] == "NOT_FOUND_NOT_FINAL"
    assert lookup.json()["original_receipt"] is None
    assert physical_snapshot(engine) == before
