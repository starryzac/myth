"""Actual shared lifecycle hook forwarding; synthetic adapter, no PG claim."""

from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.db.full_models import FullPolicy, FullPolicyCommand, FullPolicyVersion
from app.services import full_asset_action_rechecks as adapter
from app.services.full_policy_lifecycle import _record_command
from app.tests.test_execution_domain import NOW, USER
from sqlalchemy.orm import Session


def test_new_asset_lifecycle_record_rechecks_exact_owner_policy_epoch_and_original_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = FullPolicy(
        id=UUID(int=201),
        user_id=USER,
        epoch_id=UUID(int=202),
        template_name="AssetAuthorizationPolicy",
        status="REVOKED",
    )
    version = FullPolicyVersion(id=UUID(int=203), content_hash="a" * 64)
    recheck = MagicMock(return_value=([UUID(int=210)], [UUID(int=211), UUID(int=211)]))
    monkeypatch.setattr(adapter, "recheck_full_asset_actions", recheck)
    session = MagicMock(spec=Session)
    original = {"kind": "REVOKE", "key": "original-asset-revoke"}
    result = _record_command(
        session, policy, version, None, "REVOKE", "original-asset-revoke", original, "ACTIVE", NOW
    )
    recheck.assert_called_once_with(
        session, USER, policy.id, policy.epoch_id, NOW, result.command_id
    )
    assert result.invalidated_action_ids == [UUID(int=210)]
    assert result.inflight_action_ids == [UUID(int=211)]
    assert result.action_dependencies_supported is False
    recorded = session.add.call_args.args[0]
    assert isinstance(recorded, FullPolicyCommand)
    assert recorded.id == result.command_id and recorded.request == original
    assert recorded.result == result.model_dump(mode="json")
    assert policy.updated_at == NOW


def test_asset_recheck_failure_rolls_back_with_caller_and_never_records_false_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = FullPolicy(
        id=UUID(int=201),
        user_id=USER,
        epoch_id=UUID(int=202),
        template_name="AssetAuthorizationPolicy",
        status="REVOKED",
    )
    version = FullPolicyVersion(id=UUID(int=203), content_hash="a" * 64)
    monkeypatch.setattr(
        adapter, "recheck_full_asset_actions", MagicMock(side_effect=RuntimeError("source failed"))
    )
    session = MagicMock(spec=Session)
    with pytest.raises(RuntimeError, match="source failed"):
        _record_command(
            session, policy, version, None, "REVOKE", "original-asset-revoke", {}, "ACTIVE", NOW
        )
    session.add.assert_not_called()
    session.flush.assert_not_called()
