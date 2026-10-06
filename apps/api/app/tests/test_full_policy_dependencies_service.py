"""Actual adapter gates with source doubles; no database execution."""

from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.db.models import Policy, PolicyVersion
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services import full_policy_dependencies as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_policy_dependencies import EPOCH, FIRST, NOW, SECOND, USER, fixture


def source(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[str]]:
    data = fixture()
    session = MagicMock()
    session.no_autoflush = nullcontext()
    session.get.return_value = SimpleNamespace(is_simulated=True)
    session.scalar.side_effect = [SimpleNamespace(epoch_id=EPOCH), 2, 3]
    session.scalars.return_value = [FIRST, SECOND]
    calls: list[str] = []
    monkeypatch.setattr(service, "_read_snapshot", lambda _s: calls.append("snapshot"))
    monkeypatch.setattr(service, "audit_read_scope", lambda _s: nullcontext())
    monkeypatch.setattr(
        service, "current_audit_epoch", lambda *_: SimpleNamespace(id=EPOCH, status="OPEN")
    )
    monkeypatch.setattr(
        service,
        "verify_audit_chain",
        lambda *_: SimpleNamespace(
            status="VALID", model_dump=lambda **_: {"status": "VALID", "epoch_id": str(EPOCH)}
        ),
    )

    def binding(_s: Any, _user: Any, identity: Any, _now: Any) -> tuple[Any, Any, Any, bool]:
        calls.append(str(identity))
        original = next(row for row in data.policies if row.policy_id == identity)
        version = SimpleNamespace(
            id=original.version_id,
            confirmed_at=NOW,
            valid_from=NOW,
            valid_until=None,
            configuration=original.configuration,
            content_hash=original.configuration_hash,
            impact_analysis={"reference_snapshots": original.recorded_references},
        )
        policy = SimpleNamespace(
            id=identity, name=original.name, template_name=original.template_name, status="ACTIVE"
        )
        return policy, [version], [], True

    monkeypatch.setattr(service, "_binding", binding)
    monkeypatch.setattr(
        service,
        "_references",
        lambda _s, _u, config, _now: (
            next(row.current_references for row in data.policies if row.configuration == config),
            [],
        ),
    )
    # Configurations differ by their dependency list, so the sources are unambiguous.
    return session, calls


def test_actual_gates_precede_complete_original_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    session, calls = source(monkeypatch)
    result = service.read_full_policy_dependencies(session, USER, FIRST, NOW)
    assert calls == ["snapshot", str(FIRST), str(SECOND)]
    assert result.status == "COMPLETE_CURRENT_DECLARATION_GRAPH" and not result.review_required
    session.commit.assert_not_called()
    session.flush.assert_not_called()


def test_invalid_audit_prevents_using_any_policy_as_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, calls = source(monkeypatch)
    monkeypatch.setattr(
        service,
        "verify_audit_chain",
        lambda *_: SimpleNamespace(status="INVALID", model_dump=lambda **_: {"status": "INVALID"}),
    )
    result = service.read_full_policy_dependencies(session, USER, FIRST, NOW)
    assert calls == ["snapshot"] and result.status == "UNKNOWN"
    assert result.current_policy_count == 2 and not result.policies


def test_unavailable_actual_chain_is_not_silently_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    session, _ = source(monkeypatch)

    def absent(*_: Any) -> Any:
        raise PolicyLifecycleError("INVALID_FULL_POLICY_SOURCE", "original tampered", 409)

    monkeypatch.setattr(service, "_binding", absent)
    result = service.read_full_policy_dependencies(session, USER, FIRST, NOW)
    assert result.status == "UNKNOWN" and result.current_policy_count == 2
    assert result.captured_policy_count == 0 and len(result.reasons) >= 2


@pytest.mark.parametrize("case", ["foreign", "archived", "missing-open"])
def test_foreign_or_historical_roots_never_become_current_dependencies(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    session, _ = source(monkeypatch)
    if case == "foreign":
        session.scalar.side_effect = [None]
    elif case == "archived":
        session.scalar.side_effect = [SimpleNamespace(epoch_id=SECOND)]
    else:
        monkeypatch.setattr(service, "current_audit_epoch", lambda *_: None)
    with pytest.raises(PolicyLifecycleError) as error:
        service.read_full_policy_dependencies(session, USER, FIRST, NOW)
    assert error.value.status_code == (404 if case == "foreign" else 409)


def test_unclean_snapshot_is_rejected_without_original_queries() -> None:
    session = MagicMock()
    session.new = {object()}
    with pytest.raises(PolicyLifecycleError, match="RR"):
        service.read_full_policy_dependencies(session, USER, FIRST, NOW)
    session.scalar.assert_not_called()


def test_expired_mvp_uses_original_lifecycle_and_binding_not_persisted_active() -> None:
    version_id = SECOND
    config = validate_configuration(
        {"type": "emergency_buffer", "name": "纯到期引用风险", "amount_cents": 100}
    )
    digest = configuration_hash(config)
    policy = Policy(id=FIRST, user_id=USER, status="ACTIVE")
    version = PolicyVersion(
        id=version_id,
        policy_id=FIRST,
        user_id=USER,
        created_at=NOW - timedelta(days=2),
        confirmed_at=NOW - timedelta(days=1),
        valid_until=NOW,
        configuration=config,
        content_hash=digest,
        confirmation={
            "accepted": True,
            "reviewed_hash": digest,
            "user_id": str(USER),
            "policy_id": str(FIRST),
            "version_id": str(version_id),
            "confirmed_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    ref: dict[str, Any] = {
        "kind": "MVP_POLICY",
        "id": str(FIRST),
        "version_ids": [str(version_id)],
        "snapshot": {"status": "ACTIVE"},
        "binding_hash": configuration_hash(
            {"id": str(FIRST), "version_id": str(version_id), "hash": digest}
        ),
    }
    session = MagicMock()
    session.get.side_effect = [policy, version]
    assert service._reference_statuses(session, [ref], USER, NOW) == {str(FIRST): "EXPIRED"}
    assert ref["snapshot"]["status"] == policy.status == "ACTIVE"
    version.user_id = EPOCH
    session.get.side_effect = [policy, version]
    with pytest.raises(ValueError, match="lifecycle originals"):
        service._reference_statuses(session, [ref], USER, NOW)
