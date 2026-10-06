"""Original FULL goal confirmation lookup retains historical receipts, with zero writes."""

from copy import deepcopy
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from app.db.models import EvidenceItem, PolicyVersion
from app.tests.test_full_goals_api import confirmed_existing_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_original_full_goal_lookup_keeps_old_receipt_after_change_and_rejects_tamper(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, request = confirmed_existing_goal(client, engine)
    request["idempotency_key"] = "goal/original-key"
    url = f"/api/v1/goals/{goal_id}/full-model"
    lookup_url = url + "/commands/by-key/" + quote(request["idempotency_key"], safe="")
    before = physical_snapshot(engine)
    missing = client.get(lookup_url)
    assert missing.status_code == 200
    assert missing.json()["status"] == "NOT_FOUND"
    assert missing.json()["not_found_is_final"] is False
    assert client.get(lookup_url + "?now=2027-01-01").status_code == 422
    assert physical_snapshot(engine) == before
    preview = client.post(
        url + "/preview",
        json={key: request[key] for key in ("configuration", "expected_version_id")},
    )
    assert preview.status_code == 200
    values = preview.json()
    request.update(
        configuration=values["full_configuration"],
        reviewed_full_hash=values["full_configuration_hash"],
        reviewed_base_hash=values["base_configuration_hash"],
        accepted=True,
    )
    confirmed = client.post(url + "/confirm", json=request)
    assert confirmed.status_code == 200
    original_receipt = confirmed.json()
    saved = physical_snapshot(engine)
    first = client.get(lookup_url)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["status"] == "RECORDED"
    assert result["bank_authority"] is result["receipt_is_current_authority"] is False
    assert result["record"]["original_request"] == request
    assert result["record"]["receipt"] == original_receipt | {"idempotent_replay": True}
    assert client.get(lookup_url).json() == result
    assert client.get(lookup_url + "?user_id=" + str(uuid4())).status_code == 422
    assert client.get(lookup_url.replace(goal_id, str(uuid4()), 1)).status_code == 404
    assert physical_snapshot(engine) == saved
    changed = deepcopy(request)
    changed["expected_version_id"] = original_receipt["lifecycle"]["current_version_id"]
    changed["idempotency_key"] = "goal/later-key"
    changed["configuration"]["deferral_cost_cents_per_day"] += 1
    second_preview = client.post(
        url + "/preview",
        json={key: changed[key] for key in ("configuration", "expected_version_id")},
    )
    assert second_preview.status_code == 200
    changed["configuration"] = second_preview.json()["full_configuration"]
    changed["reviewed_full_hash"] = second_preview.json()["full_configuration_hash"]
    changed["reviewed_base_hash"] = second_preview.json()["base_configuration_hash"]
    assert client.post(url + "/confirm", json=changed).status_code == 200
    after_change = physical_snapshot(engine)
    assert client.get(lookup_url).json() == result
    assert physical_snapshot(engine) == after_change
    with pytest.raises(IntegrityError, match="PolicyVersion is immutable"):
        with Session(engine) as session, session.begin():
            version = session.get(
                PolicyVersion, UUID(original_receipt["lifecycle"]["current_version_id"])
            )
            assert version is not None
            altered = deepcopy(version.impact_analysis)
            altered["lifecycle_result"]["previous_version_id"] = str(uuid4())
            version.impact_analysis = altered
    assert physical_snapshot(engine) == after_change
    assert client.get(lookup_url).json() == result
    with Session(engine) as session, session.begin():
        proof = session.get(EvidenceItem, UUID(original_receipt["evidence_id"]))
        assert proof is not None
        proof.content_hash = "0" * 64
    tampered = physical_snapshot(engine)
    assert client.get(lookup_url).status_code == 409
    assert physical_snapshot(engine) == tampered
    # The evidence and request are never silently regenerated from the current version.
    with Session(engine) as session:
        evidence = session.get(EvidenceItem, UUID(original_receipt["evidence_id"]))
        assert evidence is not None
        assert evidence.content["full_hash"] == request["reviewed_full_hash"]
