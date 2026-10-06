"""W1 DEVELOPMENT risks, never the 24 frozen samples or financial acceptance.

Pure contracts do not connect to a database. The three explicitly marked real
PostgreSQL nodes use the existing owned demo_engine fixture and original services;
their candidate source and collection are not evidence that those nodes passed.
"""

import copy
import hashlib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from app.db.base import Base
from app.db.full_models import CommandDeliveryAttempt, CommandInbox, CommandOutbox
from app.db.models import Account, ActionPlan, PolicyVersion
from app.domain.policy_configuration import configuration_hash
from app.services import scenario_service_steps as service
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_references import canonical, resolve_inputs
from app.services.scenario_runner import ScenarioRunner
from app.services.scenario_types import InitialState, Scenario, ScenarioStep
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_fixed_purchase_clock import NOW, prepare_fixed
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

NEW_KINDS = (
    "LOOKUP_ACCOUNT",
    "READ_ACTION",
    "READ_POLICY",
    "SNAPSHOT",
    "CHANGE_POLICY",
    "SUSPEND_POLICY",
    "REVOKE_POLICY",
    "CREATE_GOAL",
    "RUN_RECOVERY",
    "READ_RECOVERY",
    "ISSUE_EARLY_QUOTE",
    "DECLARE_POLICY",
    "CONFIRM_POLICY",
)


def input_contract(kind: str) -> tuple[type[service.Inputs], dict[str, Any]]:
    identity = str(uuid4())
    values: dict[str, tuple[type[service.Inputs], dict[str, Any]]] = {
        "LOOKUP_ACCOUNT": (service.LookupAccount, {"external_ref": "TOOL_ONLY-account"}),
        "READ_ACTION": (service.ActionIdentity, {"action_id": identity}),
        "READ_POLICY": (service.PolicyIdentity, {"policy_id": identity}),
        "SNAPSHOT": (service.Inputs, {}),
        "CHANGE_POLICY": (
            service.PolicyChange,
            {
                "policy_id": identity,
                "expected_version_id": str(uuid4()),
                "configuration": {"TOOL_ONLY": "not a validated financial policy"},
                "reviewed_hash": "a" * 64,
                "accepted": True,
                "reason": "TOOL_ONLY exact review",
                "idempotency_key": "TOOL_ONLY-change",
            },
        ),
        "SUSPEND_POLICY": (
            service.PolicyState,
            {"policy_id": identity, "expected_version_id": str(uuid4())},
        ),
        "REVOKE_POLICY": (
            service.PolicyState,
            {"policy_id": identity, "expected_version_id": str(uuid4())},
        ),
        "CREATE_GOAL": (
            service.CreateGoal,
            {
                "policy_id": identity,
                "expected_version_id": str(uuid4()),
                "account_id": str(uuid4()),
            },
        ),
        "RUN_RECOVERY": (service.RecoveryKey, {"idempotency_key": "TOOL_ONLY-recovery"}),
        "READ_RECOVERY": (service.RecoveryIdentity, {"decision_run_id": identity}),
        "ISSUE_EARLY_QUOTE": (service.PositionIdentity, {"position_id": identity}),
        "DECLARE_POLICY": (
            service.PolicyDeclaration,
            {
                "configuration": {
                    "type": "asset_authorization",
                    "scope": "general_idle_funds",
                    "allowed_asset_classes": ["CASH_MGMT_T0"],
                    "max_auto_managed_cents": 159117,
                    "single_action_cap_cents": 67003,
                    "max_redemption_delay_days": 0,
                    "max_lock_days": 0,
                },
                "idempotency_key": "TOOL_ONLY-declaration",
                "expected_epoch_id": identity,
            },
        ),
        "CONFIRM_POLICY": (
            service.ProposalConfirmation,
            {"proposal_id": identity, "reviewed_hash": "a" * 64, "accepted": True},
        ),
    }
    return values[kind]


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_registered_kind_has_a_bounded_input_contract(kind: str) -> None:
    assert set(NEW_KINDS) == service.KINDS
    model, values = input_contract(kind)
    parsed = model.model_validate(values)
    assert set(parsed.model_dump()) == set(values)
    step = ScenarioStep.model_validate(
        {"step_id": "pure-contract", "kind": kind, "at": NOW, "inputs": values}
    )
    assert step.kind == kind and step.fault == "NONE"


@pytest.mark.parametrize("kind", NEW_KINDS)
@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "principal_id",
        "clock",
        "amount_cents",
        "balance_cents",
        "autonomy_level",
        "result",
    ],
)
def test_principal_financial_overrides_are_not_step_inputs(kind: str, field: str) -> None:
    model, values = input_contract(kind)
    with pytest.raises(ValidationError):
        model.model_validate(values | {field: "TOOL_ONLY hostile input"})


@pytest.mark.parametrize("kind", NEW_KINDS + ("EXECUTE_ACTION", "EXTERNAL_FACT"))
@pytest.mark.parametrize("fault", ["NONE", "DROP_BANK_RESPONSE", "FAIL_APPLICATION_PROJECTION"])
def test_only_original_implemented_fault_kind_pairs_are_admitted(kind: str, fault: str) -> None:
    values = {"step_id": "pure-fault", "kind": kind, "at": NOW, "inputs": {}, "fault": fault}
    admitted = (
        fault == "NONE"
        or kind == "EXECUTE_ACTION"
        or (kind == "EXTERNAL_FACT" and fault == "FAIL_APPLICATION_PROJECTION")
    )
    if admitted:
        assert ScenarioStep.model_validate(values).fault == fault
    else:
        with pytest.raises(ValidationError, match="fault-kind pair"):
            ScenarioStep.model_validate(values)


@pytest.mark.parametrize("accepted", [False, 1, 0, "true", None])
def test_policy_change_requires_the_original_explicit_boolean_review(accepted: Any) -> None:
    model, values = input_contract("CHANGE_POLICY")
    with pytest.raises(ValidationError):
        model.model_validate(values | {"accepted": accepted})


@pytest.mark.parametrize("reviewed_hash", ["", "A" * 64, "a" * 63, "a" * 65, 123])
def test_policy_change_does_not_accept_an_opaque_or_wrong_shape_review(reviewed_hash: Any) -> None:
    model, values = input_contract("CHANGE_POLICY")
    with pytest.raises(ValidationError):
        model.model_validate(values | {"reviewed_hash": reviewed_hash})


@pytest.mark.parametrize(
    "url,naive",
    [
        ("postgresql+psycopg://unused@127.0.0.1:54329/bounded_funds", False),
        ("postgresql+psycopg://unused@remote.invalid:54329/bf_test_" + "a" * 32, False),
        ("postgresql+psycopg://unused@127.0.0.1:5432/bf_test_" + "a" * 32, False),
        ("sqlite:///bf_test_" + "a" * 32, False),
        ("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "a" * 32, True),
    ],
)
def test_local_owned_database_and_aware_clock_guards_precede_database_io(
    url: str, naive: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("TOOL_ONLY invalid context must not open a session")

    monkeypatch.setattr(service, "Session", forbidden)
    engine = cast(Engine, SimpleNamespace(url=make_url(url)))
    clock = NOW.replace(tzinfo=None) if naive else NOW
    with pytest.raises((PolicyLifecycleError, ValueError)):
        service.dispatch_service_step(engine, uuid4(), "SNAPSHOT", {}, clock)


@pytest.mark.parametrize(
    "reference",
    [
        {"$ref": "lookup/result/account/id"},
        {"$ref": {"step_id": "lookup", "pointer": "/result/account/id", "amount": 999}},
        {"$ref": {"step_id": "lookup", "pointer": "/result/account/id"}, "user_id": "foreign"},
        {"$ref": {"step_id": "later", "pointer": "/result/account/id"}},
        {"$ref": {"step_id": "lookup", "pointer": "/error/code"}},
        {"$ref": {"step_id": "lookup", "pointer": "/result/~2id"}},
        {"$ref": {"step_id": "lookup", "pointer": "/result/missing"}},
    ],
)
def test_only_strict_backward_original_result_object_references_are_resolved(
    reference: Any,
) -> None:
    previous = {"lookup": {"result": {"account": {"id": str(uuid4())}}}}
    with pytest.raises(ValueError):
        resolve_inputs({"account_id": reference}, previous)


def test_original_result_reference_binds_actual_bytes_and_does_not_alias_them() -> None:
    row = {"result": {"policy": {"id": str(uuid4()), "configuration": {"cap": 17}}}}
    previous = {"read": copy.deepcopy(row)}
    inputs = {"policy": {"$ref": {"step_id": "read", "pointer": "/result/policy"}}}
    resolved, refs = resolve_inputs(inputs, previous)
    assert resolved["policy"] == row["result"]["policy"]
    assert refs[0]["original_row_sha256"] == hashlib.sha256(canonical(row)).hexdigest()
    assert refs[0]["value_sha256"] == hashlib.sha256(canonical(resolved["policy"])).hexdigest()
    resolved["policy"]["configuration"]["cap"] = 999
    assert previous["read"] == row


def original_rows(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    """All physical originals, including delivery/FULL planning tables and migration head."""
    from app.db.catalog_models import ProductCatalogVersion
    from app.db.full_models import FullPolicy, FullPolicyCommand, FullPolicyVersion

    delivery_tables = {
        CommandOutbox.__tablename__,
        CommandInbox.__tablename__,
        CommandDeliveryAttempt.__tablename__,
    }
    full_policy_tables = {
        FullPolicy.__tablename__,
        FullPolicyVersion.__tablename__,
        FullPolicyCommand.__tablename__,
    }
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            physical = set(
                connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
                )
            )
            assert (
                len(
                    set(Base.metadata.tables)
                    - delivery_tables
                    - full_policy_tables
                    - {ProductCatalogVersion.__tablename__}
                )
                == 23
            )
            assert len(Base.metadata.tables) == 30
            assert physical == set(Base.metadata.tables) | {"alembic_version"}
            rows = {
                table.name: [
                    dict(row)
                    for row in connection.execute(select(table).order_by(table.c.id)).mappings()
                ]
                for table in Base.metadata.sorted_tables
            }
            rows["alembic_version"] = [
                dict(row)
                for row in connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                ).mappings()
            ]
    assert len(rows) == 31 and sum(map(len, rows.values())) > 0
    return rows


def case(engine: Engine, steps: list[ScenarioStep], identity: str) -> Scenario:
    with Session(engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None and epoch.status == "OPEN"
        epoch_id = epoch.id
    return Scenario(
        scenario_id=identity,
        purpose="DEVELOPMENT",
        dataset_id="w1-service-step-risk-tests-not-frozen24",
        family_id="integration-risk-only",
        initial_state=InitialState(expected_epoch_id=epoch_id),
        steps=steps,
    )


def step(
    identity: str, kind: str, second: int, inputs: dict[str, Any], **extra: Any
) -> ScenarioStep:
    return ScenarioStep.model_validate(
        {"step_id": identity, "kind": kind, "at": NOW + timedelta(seconds=second), "inputs": inputs}
        | extra
    )


def ref(identity: str, pointer: str) -> dict[str, Any]:
    return {"$ref": {"step_id": identity, "pointer": "/result/" + pointer}}


@pytest.mark.integration
def test_lookup_action_policy_snapshot_and_refused_overrides_keep_all_physical24_rows(
    demo_engine: Engine,
) -> None:
    prepared = prepare_fixed(demo_engine)
    assert prepared.effect.policy_id is not None
    with Session(demo_engine) as session:
        account = session.scalar(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        )
        assert account is not None
        external_ref, account_id = account.external_ref, account.id
    before = original_rows(demo_engine)
    steps = [
        step("lookup", "LOOKUP_ACCOUNT", 3, {"external_ref": external_ref}),
        step("action", "READ_ACTION", 4, {"action_id": str(prepared.action_id)}),
        step("policy", "READ_POLICY", 5, {"policy_id": str(prepared.effect.policy_id)}),
        step("snapshot", "SNAPSHOT", 6, {}),
    ]
    for index, field in enumerate(
        (
            "user_id",
            "principal_id",
            "clock",
            "amount_cents",
            "balance_cents",
            "autonomy_level",
            "result",
        )
    ):
        steps.append(
            step(
                "refuse-" + field,
                "LOOKUP_ACCOUNT",
                7 + index,
                {"external_ref": external_ref, field: str(uuid4())},
                expected_error={"code": "INVALID_SCENARIO_STEP", "status_code": 422},
            )
        )
    steps.append(step("read-after-refusals", "SNAPSHOT", 14, {}))
    result = ScenarioRunner(demo_engine, DEMO_USER_ID).run(case(demo_engine, steps, "readonly24"))
    assert result.status == "EXECUTED" and len(result.steps) == len(steps)
    assert result.steps[0]["result"]["account"]["id"] == str(account_id)
    assert result.steps[1]["result"]["effect_hash"] == prepared.effect_hash
    assert result.steps[2]["result"]["versions"][0]["id"] == str(prepared.effect.policy_version_id)
    for row in result.steps[4:-1]:
        assert row["expected_error_matched"] is True
        assert row["error"]["code"] == "INVALID_SCENARIO_STEP"
    observed = result.steps[-1]["result"]
    assert observed["read_only"] is True and observed["isolation_level"] == "REPEATABLE READ"
    assert observed["table_count"] == 30 and set(observed["tables"]) == set(Base.metadata.tables)
    with pytest.raises(PolicyLifecycleError) as foreign:
        ScenarioRunner(demo_engine, uuid4()).run(case(demo_engine, steps[:1], "foreign-principal"))
    assert foreign.value.code == "NOT_FOUND" and foreign.value.status_code == 404
    assert original_rows(demo_engine) == before


@pytest.mark.integration
def test_original_goal_confirmation_then_creation_has_zero_ownership_and_no_cash_operation(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    before = original_rows(demo_engine)
    with Session(demo_engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        account = session.scalar(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "GOAL")
        )
        assert epoch is not None and account is not None
        epoch_id, external_ref = epoch.id, account.external_ref
    steps = [
        step(
            "prepare",
            "PREPARE_TEMPLATE",
            0,
            {"kind": "CAR_GOAL", "expected_epoch_id": str(epoch_id)},
        ),
        step(
            "wrong-review",
            "CONFIRM_TEMPLATE",
            1,
            {
                "proposal_id": ref("prepare", "proposal_id"),
                "reviewed_hash": "0" * 64,
                "accepted": True,
            },
            expected_error={"code": "REVIEW_MISMATCH", "status_code": 409},
        ),
        step(
            "confirm",
            "CONFIRM_TEMPLATE",
            2,
            {
                "proposal_id": ref("prepare", "proposal_id"),
                "reviewed_hash": ref("prepare", "configuration_hash"),
                "accepted": True,
            },
        ),
        step("lookup", "LOOKUP_ACCOUNT", 3, {"external_ref": external_ref}),
        step("policy", "READ_POLICY", 4, {"policy_id": ref("confirm", "policy_id")}),
        step(
            "create",
            "CREATE_GOAL",
            5,
            {
                "policy_id": ref("confirm", "policy_id"),
                "expected_version_id": ref("confirm", "current_version_id"),
                "account_id": ref("lookup", "account/id"),
            },
        ),
    ]
    result = ScenarioRunner(demo_engine, DEMO_USER_ID).run(case(demo_engine, steps, "zero-goal"))
    assert result.status == "EXECUTED" and len(result.steps) == 6
    assert result.steps[1]["expected_error_matched"] is True
    confirmed, versions = result.steps[2]["result"], result.steps[4]["result"]["versions"]
    assert len(versions) == 1 and versions[0]["id"] == confirmed["current_version_id"]
    assert versions[0]["confirmation"]["accepted"] is True
    goal = result.steps[5]["result"]["goal"]
    assert goal["policy_id"] == confirmed["policy_id"]
    assert goal["policy_version_id"] == confirmed["current_version_id"]
    assert goal["allocated_cents"] == 0
    after = original_rows(demo_engine)
    for name in (
        "accounts",
        "asset_positions",
        "transactions",
        "bank_operations",
        "action_receipts",
    ):
        assert after[name] == before[name]
    old_postings = {row["id"]: row for row in before["simulated_bank_postings"]}
    assert all(row in after["simulated_bank_postings"] for row in old_postings.values())
    new_postings = [
        row for row in after["simulated_bank_postings"] if row["id"] not in old_postings
    ]
    assert {row["ledger_key"] for row in new_postings} == {
        "GOAL_CASH:" + goal["id"],
        "GOAL_PRINCIPAL:" + goal["id"],
    }
    assert len(new_postings) == 2
    for row in new_postings:
        assert row["entry_kind"] == "OPENING"
        assert row["delta_cents"] == row["balance_before_cents"] == row["balance_after_cents"] == 0
    ownership = [
        row["content"]
        for row in after["evidence_items"]
        if row["source_type"] == "SIMULATED_GOAL_OWNERSHIP"
        and row["content"].get("goal_id") == goal["id"]
    ]
    assert len(ownership) == 1
    assert ownership[0]["cash_owned_cents"] == ownership[0]["principal_owned_cents"] == 0
    assert ownership[0]["allocated_cents"] == 0 and ownership[0]["position_ids"] == []
    assert after["alembic_version"] == before["alembic_version"]
    with Session(demo_engine) as session:
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"


@pytest.mark.integration
def test_real_policy_change_invalidates_old_action_without_replacing_its_effect_or_bank_rows(
    demo_engine: Engine,
) -> None:
    prepared = prepare_fixed(demo_engine)
    assert prepared.effect.policy_id is not None and prepared.effect.policy_version_id is not None
    with Session(demo_engine) as session:
        version = session.get(PolicyVersion, prepared.effect.policy_version_id)
        action = session.get(ActionPlan, prepared.action_id)
        assert version is not None and action is not None
        old_request, old_request_hash = copy.deepcopy(action.request), action.request_hash
        old_version = copy.deepcopy(version.configuration)
    changed = old_version | {"single_action_cap_cents": 40_000}
    change_inputs = {
        "policy_id": str(prepared.effect.policy_id),
        "expected_version_id": str(prepared.effect.policy_version_id),
        "configuration": changed,
        "reviewed_hash": configuration_hash(changed),
        "accepted": True,
        "reason": "W1 real-service risk: lower the single-action cap after preparation",
        "idempotency_key": "w1-step-policy-change",
    }
    before = original_rows(demo_engine)
    steps = [
        step("change", "CHANGE_POLICY", 3, change_inputs),
        step("read-policy", "READ_POLICY", 4, {"policy_id": str(prepared.effect.policy_id)}),
        step("read-old-action", "READ_ACTION", 5, {"action_id": str(prepared.action_id)}),
        step(
            "stale-change",
            "CHANGE_POLICY",
            6,
            change_inputs | {"idempotency_key": "w1-step-stale-version"},
            expected_error={"code": "STALE_POLICY_VERSION", "status_code": 409},
        ),
        step(
            "old-execute-refused",
            "EXECUTE_ACTION",
            7,
            {"action_id": str(prepared.action_id)},
            expected_error={"code": "INVALID_ACTION_STATE", "status_code": 409},
        ),
        step("continued-read", "READ_POLICY", 8, {"policy_id": str(prepared.effect.policy_id)}),
    ]
    result = ScenarioRunner(demo_engine, DEMO_USER_ID).run(
        case(demo_engine, steps, "policy-change")
    )
    assert result.status == "EXECUTED" and len(result.steps) == 6
    assert str(prepared.action_id) in result.steps[0]["result"]["invalidated_action_ids"]
    versions = result.steps[1]["result"]["versions"]
    assert len(versions) == 2
    assert versions[0]["id"] == str(prepared.effect.policy_version_id)
    assert versions[0]["configuration"] == old_version
    assert versions[1]["configuration"] == changed
    assert versions[1]["previous_hash"] == versions[0]["content_hash"]
    observed = result.steps[2]["result"]
    assert observed["status"] == "INVALIDATED" and observed["receipt"] is None
    assert observed["effect_hash"] == prepared.effect_hash
    assert observed["effect"] == prepared.effect.model_dump(mode="json")
    assert all(result.steps[index]["expected_error_matched"] is True for index in (3, 4))
    assert result.steps[-1]["result"]["versions"] == versions
    with Session(demo_engine) as session:
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None and action.status == "INVALIDATED"
        assert action.request == old_request and action.request_hash == old_request_hash
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
    after = original_rows(demo_engine)
    for name in (
        "accounts",
        "asset_positions",
        "transactions",
        "bank_operations",
        "action_receipts",
        "simulated_bank_postings",
        "simulated_bank_redemptions",
        "alembic_version",
    ):
        assert after[name] == before[name]
