"""Real isolated PG planning metadata risks; root alone runs these nodes."""

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.base import Base
from app.db.full_models import FullPolicy, FullPolicyCommand, FullPolicyVersion
from app.db.models import Account, EvidenceItem, Goal, User
from app.domain.full_policy_configuration import TemplateName
from app.domain.policy_configuration import configuration_hash
from app.services import full_policy_lifecycle as full
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import confirmed_policy, snapshot
from app.tests.test_full_policy_configuration import EXAMPLES
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def body(
    template: TemplateName = "InterventionPolicy",
    configuration: dict[str, Any] | None = None,
    *,
    key: str | None = None,
) -> full.FullCreateRequest:
    candidate = deepcopy(EXAMPLES[template] if configuration is None else configuration)
    canonical = full.canonical_candidate(template, candidate)
    return full.FullCreateRequest(
        template_name=template,
        configuration=candidate,
        reviewed_hash=configuration_hash(canonical),
        accepted=True,
        reason="真实模拟用户明确确认规划规则",
        idempotency_key=key or str(uuid4()),
    )


def create(
    engine: Engine, request: full.FullCreateRequest | None = None
) -> full.FullLifecycleResult:
    with Session(engine) as session, session.begin():
        return full.confirm_full_policy(session, DEMO_USER_ID, request or body(), SEED_AS_OF)


@contextmanager
def readonly(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


def old_rows(engine: Engine, evidence_ids: set[UUID]) -> dict[str, Any]:
    with engine.connect() as connection:
        return {
            table.name: [
                dict(row)
                for row in connection.execute(
                    select(table).where(table.c.id.in_(evidence_ids)).order_by(table.c.id)
                    if table.name == "evidence_items"
                    else select(table).order_by(table.c.id)
                ).mappings()
            ]
            for table in Base.metadata.sorted_tables
            if table.name not in {"full_policies", "full_policy_versions", "full_policy_commands"}
        }


def state_request(version: UUID, key: str) -> full.FullStateRequest:
    return full.FullStateRequest(
        expected_version_id=version, reason="明确停止规则", idempotency_key=key
    )


def unfunded_goal_reference_fixture(session: Session) -> UUID:
    """Actual owned row for relationship tests; no imported ownership or paid funds."""
    configuration = {
        "type": "goal_saving",
        "name": "未拨款的引用目标",
        "target_cents": 1000000,
        "deadline": (SEED_AS_OF.date() + timedelta(days=365)).isoformat(),
        "monthly_contribution": {
            "min_cents": 0,
            "target_cents": 10000,
            "max_cents": 10000,
        },
    }
    policy_id, version_id = confirmed_policy(session, configuration, SEED_AS_OF)
    account = session.scalar(select(Account).where(Account.account_type == "GOAL"))
    assert account is not None
    row = Goal(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        created_at=SEED_AS_OF,
        policy_id=policy_id,
        policy_version_id=version_id,
        account_id=account.id,
        name="未拨款的引用目标",
        target_cents=1000000,
        allocated_cents=0,
        deadline=SEED_AS_OF.date() + timedelta(days=365),
        monthly_min_cents=0,
        monthly_target_cents=10000,
        monthly_max_cents=10000,
    )
    session.add(row)
    session.flush()
    return row.id


def test_eight_templates_persist_only_planning_consent_without_changing_old_financial_rows(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        first = unfunded_goal_reference_fixture(session)
        second = unfunded_goal_reference_fixture(session)
        goal = session.get(Goal, first)
        cash = session.scalar(
            select(Account).where(Account.account_type == "CASH").order_by(Account.id)
        )
        assert goal is not None and cash is not None
        protected, cash_id = goal.policy_id, cash.id
        evidence_ids = set(session.scalars(select(EvidenceItem.id)))
    before = old_rows(boundary_engine, evidence_ids)
    created: dict[TemplateName, full.FullLifecycleResult] = {}
    for template in full.FULL_TEMPLATES:
        configuration = deepcopy(EXAMPLES[template])
        if template == "DatedExpensePolicy":
            configuration["must_not_reduce_policy_ids"] = [str(protected)]
        elif template == "PeriodicTransferPolicy":
            configuration["source_account_id"] = str(cash_id)
            configuration["payee_id"] = "synthetic-landlord-001"
        elif template == "AssetAuthorizationPolicy":
            configuration["goal_id"] = str(first)
        elif template == "RecoveryPolicy":
            configuration["goal_id"] = str(first)
            configuration["asset_policy_id"] = str(created["AssetAuthorizationPolicy"].policy_id)
        elif template == "GoalAllocationPolicy":
            configuration["goal_ids"] = [str(first), str(second)]
        elif template == "CrossGoalReallocationPolicy":
            configuration["source_goal_ids"] = [str(first), str(second)]
        result = create(boundary_engine, body(template, configuration))
        assert result.bank_authority is False and result.action_dependencies_supported is False
        created[template] = result
        with readonly(boundary_engine) as session:
            current = full.read_full_policy(session, DEMO_USER_ID, result.policy_id, SEED_AS_OF)
            assert current.planning_confirmation_valid is True
            assert current.execution_support == "NOT_IMPLEMENTED"
            assert (
                current.current_version.confirmation_evidence_status == "CURRENT_EVIDENCE_MATCHED"
            )
    assert len(created) == 8
    assert old_rows(boundary_engine, evidence_ids) == before


def test_version_confirmation_change_suspend_resume_and_idempotent_history(
    boundary_engine: Engine,
) -> None:
    original_request = body(key="original-create")
    initial = create(boundary_engine, original_request)
    with Session(boundary_engine) as session, session.begin():
        stopped = full.stop_full_policy(
            session,
            DEMO_USER_ID,
            initial.policy_id,
            state_request(initial.version_id, "stop"),
            SEED_AS_OF,
        )
        assert stopped.status == "SUSPENDED"
    resume = full.FullResumeRequest(
        expected_version_id=initial.version_id,
        reviewed_hash=initial.configuration_hash,
        accepted=True,
        reason="重新明确确认原配置",
        idempotency_key="resume",
    )
    with Session(boundary_engine) as session, session.begin():
        resumed = full.change_full_policy(
            session, DEMO_USER_ID, initial.policy_id, resume, SEED_AS_OF, resume=True
        )
    assert resumed.version_id != initial.version_id and resumed.status == "ACTIVE"
    changed = deepcopy(original_request.configuration)
    changed["name"] = "用户修改后的完整规则"
    canonical = full.canonical_candidate("InterventionPolicy", changed)
    change = full.FullChangeRequest(
        expected_version_id=resumed.version_id,
        configuration=changed,
        reviewed_hash=configuration_hash(canonical),
        accepted=True,
        reason="用户新偏好",
        idempotency_key="change",
    )
    with Session(boundary_engine) as session, session.begin():
        modified = full.change_full_policy(
            session, DEMO_USER_ID, initial.policy_id, change, SEED_AS_OF
        )
        replay = full.confirm_full_policy(session, DEMO_USER_ID, original_request, SEED_AS_OF)
    assert replay == initial and replay.receipt_is_current_authority is False
    with readonly(boundary_engine) as session:
        versions = full.list_full_versions(
            session, DEMO_USER_ID, initial.policy_id, SEED_AS_OF
        ).items
        commands = full.list_full_commands(
            session, DEMO_USER_ID, initial.policy_id, SEED_AS_OF
        ).items
        assert [version.version_number for version in versions] == [1, 2, 3]
        assert versions[-1].version_id == modified.version_id
        assert versions[1].previous_hash == versions[0].content_hash
        assert versions[2].previous_hash == versions[1].content_hash
        assert [command.command_number for command in commands] == [1, 2, 3, 4]
        assert [command.kind for command in commands] == ["CREATE", "SUSPEND", "RESUME", "CHANGE"]
        assert all(
            current.previous_hash == previous.result_hash
            for previous, current in zip(commands, commands[1:], strict=False)
        )


def test_future_effective_time_and_expiration_cannot_revive(boundary_engine: Engine) -> None:
    configuration = deepcopy(EXAMPLES["InterventionPolicy"])
    configuration.update({"valid_from": "2026-10-05", "valid_until": "2026-10-05"})
    initial = create(boundary_engine, body(configuration=configuration))
    assert initial.status == "CONFIRMED"
    start, end = SEED_AS_OF + timedelta(days=1), SEED_AS_OF + timedelta(days=2)
    with Session(boundary_engine) as session, session.begin():
        refreshed = full.refresh_full_policy_time(session, DEMO_USER_ID, start)
        assert refreshed.results[0].status == "ACTIVE"
    with Session(boundary_engine) as session, session.begin():
        expired = full.refresh_full_policy_time(session, DEMO_USER_ID, end)
        assert expired.results[0].status == "EXPIRED"
    before = snapshot(boundary_engine)
    with (
        Session(boundary_engine) as session,
        session.begin(),
        pytest.raises(PolicyLifecycleError) as captured,
    ):
        full.change_full_policy(
            session,
            DEMO_USER_ID,
            initial.policy_id,
            full.FullResumeRequest(
                expected_version_id=initial.version_id,
                reviewed_hash=initial.configuration_hash,
                accepted=True,
                reason="不得复活过期规则",
                idempotency_key="bad-resume",
            ),
            end,
            resume=True,
        )
    assert captured.value.code == "INVALID_POLICY_STATE"
    assert snapshot(boundary_engine) == before


def test_wrong_owner_stale_version_changed_key_and_missing_open_proof_are_zero_write(
    boundary_engine: Engine,
) -> None:
    request = body(key="same-key")
    initial = create(boundary_engine, request)
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session, pytest.raises(PolicyLifecycleError) as missing:
        full.read_full_policy(session, uuid4(), initial.policy_id, SEED_AS_OF)
    assert missing.value.status_code == 404
    changed = deepcopy(request.configuration)
    changed["name"] = "同键不同命令"
    with (
        Session(boundary_engine) as session,
        session.begin(),
        pytest.raises(PolicyLifecycleError) as conflict,
    ):
        full.confirm_full_policy(
            session, DEMO_USER_ID, body(configuration=changed, key="same-key"), SEED_AS_OF
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    with (
        Session(boundary_engine) as session,
        session.begin(),
        pytest.raises(PolicyLifecycleError) as stale,
    ):
        full.stop_full_policy(
            session,
            DEMO_USER_ID,
            initial.policy_id,
            state_request(uuid4(), "wrong-version"),
            SEED_AS_OF,
        )
    assert stale.value.code == "STALE_POLICY_VERSION"
    assert snapshot(boundary_engine) == before
    with Session(boundary_engine) as session, session.begin():
        version = session.get(FullPolicyVersion, initial.version_id)
        assert version is not None
        session.execute(
            delete(EvidenceItem).where(
                EvidenceItem.id == UUID(version.confirmation["confirmation_evidence_id"])
            )
        )
    tampered = snapshot(boundary_engine)
    with readonly(boundary_engine) as session, pytest.raises(PolicyLifecycleError) as source:
        full.read_full_policy(session, DEMO_USER_ID, initial.policy_id, SEED_AS_OF)
    assert source.value.code == "INVALID_FULL_POLICY_SOURCE"
    assert snapshot(boundary_engine) == tampered


def test_inserted_version_and_commands_reject_update_and_delete(boundary_engine: Engine) -> None:
    initial = create(boundary_engine)
    before = snapshot(boundary_engine)
    statements = [
        update(FullPolicyVersion)
        .where(FullPolicyVersion.id == initial.version_id)
        .values(summary="tampered"),
        delete(FullPolicyVersion).where(FullPolicyVersion.id == initial.version_id),
        update(FullPolicyCommand)
        .where(FullPolicyCommand.id == initial.command_id)
        .values(result_hash="f" * 64),
        delete(FullPolicyCommand).where(FullPolicyCommand.id == initial.command_id),
        update(FullPolicy)
        .where(FullPolicy.id == initial.policy_id)
        .values(template_name="SeasonalReservePolicy"),
        delete(FullPolicy).where(FullPolicy.id == initial.policy_id),
    ]
    for statement in statements:
        with pytest.raises(DBAPIError), Session(boundary_engine) as session, session.begin():
            session.execute(statement)
        assert snapshot(boundary_engine) == before


def test_readonly_preview_uses_actual_sources_and_leaves_all_rows_unchanged(
    boundary_engine: Engine,
) -> None:
    request = body()
    initial = create(boundary_engine, request)
    configuration = deepcopy(request.configuration)
    configuration["name"] = "只读候选"
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        preview = full.preview_full_policy_change(
            session,
            DEMO_USER_ID,
            initial.policy_id,
            full.FullPreviewRequest(
                expected_version_id=initial.version_id,
                configuration=configuration,
            ),
            SEED_AS_OF,
        )
        assert preview.configuration_hash == configuration_hash(preview.after_configuration)
        assert preview.changed_fields == ["name"]
        assert len(preview.current_fact_digest) == 64
        assert preview.current_financial_boundary.input_digest != ""
        assert preview.bank_authority is False
        assert preview.candidate_financial_status == "NOT_IMPLEMENTED"
        assert preview.delta_safe_idle_cents is None and preview.delta_goal_allocation_cents is None
        assert preview.delta_position_principal_cents is None
    assert snapshot(boundary_engine) == before


def test_reset_retains_chain_but_missing_current_evidence_is_not_current_planning_authority(
    boundary_engine: Engine,
) -> None:
    initial = create(boundary_engine)
    with Session(boundary_engine) as session:
        before_version = session.get(FullPolicyVersion, initial.version_id)
        before_command = session.get(FullPolicyCommand, initial.command_id)
        assert before_version is not None and before_command is not None
        original = deepcopy(
            (
                before_version.configuration,
                before_version.content_hash,
                before_version.confirmation,
                before_command.request_hash,
                before_command.result_hash,
            )
        )
    seed_demo(boundary_engine, reset_key="isolated-full-policy-history-reset")
    with readonly(boundary_engine) as session:
        history = full.read_full_policy(session, DEMO_USER_ID, initial.policy_id, SEED_AS_OF)
        assert history.effective_status == "ARCHIVED"
        assert history.planning_confirmation_valid is False and history.bank_authority is False
        assert (
            history.current_version.confirmation_evidence_status
            == "RETAINED_IN_VERSION_CURRENT_EVIDENCE_MISSING"
        )
        assert (
            len(full.list_full_commands(session, DEMO_USER_ID, initial.policy_id, SEED_AS_OF).items)
            == 1
        )
        retained_lookup = full.lookup_full_policy_command(
            session, DEMO_USER_ID, before_command.idempotency_key, SEED_AS_OF
        )
        assert retained_lookup.status == "RECORDED" and retained_lookup.command is not None
        assert retained_lookup.command.result["epoch_id"] == str(history.epoch_id)
        assert retained_lookup.receipt_is_current_authority is False
        version = session.get(FullPolicyVersion, initial.version_id)
        command = session.get(FullPolicyCommand, initial.command_id)
        assert version is not None and command is not None
        assert (
            version.configuration,
            version.content_hash,
            version.confirmation,
            command.request_hash,
            command.result_hash,
        ) == original
    before = snapshot(boundary_engine)
    with (
        Session(boundary_engine) as session,
        session.begin(),
        pytest.raises(PolicyLifecycleError) as archived,
    ):
        full.stop_full_policy(
            session,
            DEMO_USER_ID,
            initial.policy_id,
            state_request(initial.version_id, "old-epoch-stop"),
            SEED_AS_OF,
        )
    assert archived.value.code == "ARCHIVED_FULL_POLICY"
    assert snapshot(boundary_engine) == before


def test_command_key_lookup_recovers_original_receipt_after_change_and_is_zero_write(
    boundary_engine: Engine,
) -> None:
    original = body(key="original/create:规则")
    other_user = uuid4()
    initial = create(boundary_engine, original)
    changed = deepcopy(original.configuration)
    changed["name"] = "后来已确认的新配置"
    with Session(boundary_engine) as session, session.begin():
        session.add(
            User(
                id=other_user,
                created_at=SEED_AS_OF,
                external_ref=str(other_user),
                display_name="隔离的另一模拟用户",
                is_simulated=True,
            )
        )
        later = full.change_full_policy(
            session,
            DEMO_USER_ID,
            initial.policy_id,
            full.FullChangeRequest(
                expected_version_id=initial.version_id,
                configuration=changed,
                reviewed_hash=configuration_hash(
                    full.canonical_candidate("InterventionPolicy", changed)
                ),
                accepted=True,
                reason="明确变更",
                idempotency_key="later-change",
            ),
            SEED_AS_OF,
        )
        row = session.get(FullPolicyCommand, initial.command_id)
        assert row is not None
        request, digest = deepcopy(row.request), row.request_hash
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        found = full.lookup_full_policy_command(
            session, DEMO_USER_ID, original.idempotency_key, SEED_AS_OF
        )
        assert found.status == "RECORDED" and found.command is not None
        assert found.original_request == request and found.request_hash == digest
        assert configuration_hash(found.original_request) == digest
        assert found.command.command_id == initial.command_id
        assert found.command.version_id == initial.version_id != later.version_id
        assert found.command.result == initial.model_dump(mode="json")
        assert found.receipt_is_current_authority is False and found.bank_authority is False
        missing = full.lookup_full_policy_command(session, DEMO_USER_ID, "not-observed", SEED_AS_OF)
        assert missing.status == "NOT_FOUND" and missing.not_found_is_final is False
        assert missing.original_request is None and missing.command is None
        hidden = full.lookup_full_policy_command(
            session, other_user, original.idempotency_key, SEED_AS_OF
        )
        assert hidden.status == "NOT_FOUND" and hidden.command is None
        with pytest.raises(PolicyLifecycleError) as other:
            full.lookup_full_policy_command(session, uuid4(), original.idempotency_key, SEED_AS_OF)
        assert other.value.status_code == 404
    assert snapshot(boundary_engine) == before
