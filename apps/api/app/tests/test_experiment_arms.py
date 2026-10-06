"""Targeted real PostgreSQL risks; no formal product experiment claims.

Root must integrate/review the provider+patch first. These risk fixtures are
DEVELOPMENT probe inputs, never the actual MVP24 corpus/14 metric observations.
No P financial evaluator is mocked or relabelled. Only response loss is injected
after the real independent bank transaction has committed.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.audit_guard import audit_command_guard, gate_key
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
    User,
)
from app.db.settings import REPOSITORY_ROOT
from app.db.testing import require_test_database
from app.domain.execution import execution_effect_hash
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services import execution
from app.services.action_contracts import PrepareActionRequest, PurchaseIntent
from app.services.asset_allocation import preview_asset_allocation
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution_bank import process_operation
from app.services.execution_planning import funding_income
from app.services.external_bank_facts import ingest_external_fact
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, revoke_policy
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_boundary_service import boundary_engine, confirmed_policy, snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scripts.mvp_arm_executor import (
    SimulationContext,
    candidate_selector,
    record_confirmation,
    record_execution,
    record_prepare,
)

__all__ = ["boundary_engine", "demo_engine"]
pytestmark = pytest.mark.integration
STATE = "DEVELOPMENT_RISK_FIXTURE_NOT_FORMAL_EXPERIMENT"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def value_digest(value: Any) -> str:
    return digest(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )


def write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def provider() -> Any:
    # No fallback/mock provider; an unintegrated candidate is an explicit failure.
    assert (
        "_experiment_candidate_selector" in inspect.signature(execution.prepare_action).parameters
    ), STATE
    return importlib.import_module("app.services.experiment_arms")


class Registration:
    """Capture actual isolated SQL identity/source bytes; no expected metric numbers."""

    def __init__(
        self,
        engine: Engine,
        arm: str = "B1",
        *,
        amount: int = 10000,
        cash_account_ids: list[str] | None = None,
    ) -> None:
        self.provider = provider()
        self.engine = engine
        self.directory = REPOSITORY_ROOT / ".runtime/W1-provider-focused-root-tests" / uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=False)
        with Session(engine) as session, session.begin():
            self.policy_id, self.version_id = confirmed_policy(
                session, authorization(allowed_asset_classes=["CASH_MGMT_T0"])
            )
            epoch = current_audit_epoch(session, DEMO_USER_ID)
            assert epoch is not None and epoch.status == "OPEN"
            epoch_id = epoch.id
            accounts = list(session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID)))
            self.cash_ids = sorted(str(row.id) for row in accounts if row.account_type == "CASH")
            if cash_account_ids is not None:
                assert set(cash_account_ids) <= set(self.cash_ids)
                self.cash_ids = sorted(cash_account_ids)
            self.cash_balance = sum(
                row.balance_cents for row in accounts if str(row.id) in self.cash_ids
            )
        self.request = PrepareActionRequest(
            idempotency_key="root-probe-" + uuid4().hex,
            intent=PurchaseIntent(kind="purchase_asset", policy_id=self.policy_id),
        ).model_dump(mode="json")
        self.opportunity = "original-probe-opportunity"
        case, purpose = "ROOT-RISK-PROBE-" + uuid4().hex, "DEVELOPMENT"
        algorithm = {
            "protocol": "mvp-arm-rule-v1",
            "arm_id": arm,
            "case_id": case,
            "purpose": purpose,
            "seed_version": "mvp-301-v6",
            "cash_account_ids": self.cash_ids,
            "threshold_cents": self.cash_balance - amount,
            "manual_actions": [
                {
                    "opportunity_id": self.opportunity,
                    "intent": self.request["intent"],
                    "amount_cents": amount,
                }
            ],
            "fixed_policy_version_ids": [],
            "timezone": "UTC",
            "horizon_end_at": SEED_AS_OF.isoformat(),
        }
        implementation_files = []
        paths = [
            path
            for path in (REPOSITORY_ROOT / "apps/api/app").rglob("*.py")
            if "tests" not in path.relative_to(REPOSITORY_ROOT / "apps/api/app").parts
        ]
        paths += [
            REPOSITORY_ROOT / name
            for name in (
                "scripts/mvp_arm_executor.py",
                "scripts/mvp_observations.py",
                "scripts/mvp_trace_metrics.py",
            )
        ]
        paths += [Path(__file__).resolve()]
        for actual in sorted(set(paths)):
            raw = actual.read_bytes()
            archive = self.directory / (digest(raw) + ".source.bin")
            if not archive.exists():
                write_new(archive, raw)
            implementation_files.append(
                {
                    "original_path": actual.relative_to(REPOSITORY_ROOT).as_posix(),
                    "path": str(archive),
                    "sha256": digest(raw),
                }
            )
        self.actor = {
            "actor_id": str(uuid4()),
            "actor_kind": "SYNTHETIC_SCRIPTED_ACTOR",
            "confirmation_mode": "EXACT_EFFECT_PER_ACTION",
            "implementation_source_ref": next(
                ref
                for ref in implementation_files
                if ref["original_path"]
                == Path(__file__).resolve().relative_to(REPOSITORY_ROOT).as_posix()
            ),
        }
        artifacts: dict[str, dict[str, Any]] = {
            "input": {
                "case_id": case,
                "purpose": purpose,
                "risk_fixture_status": STATE,
                "registered_business_clocks": [
                    (SEED_AS_OF + timedelta(minutes=n)).isoformat() for n in range(5)
                ],
                "opportunities": [{"opportunity_id": self.opportunity, "request": self.request}],
            },
            "oracle": {
                "case_id": case,
                "purpose": purpose,
                "status": "NOT_A_METRIC_ORACLE",
                "risk_fixture_status": STATE,
            },
            "design": {"case_id": case, "purpose": purpose, "risk_fixture_status": STATE},
            "rule": {
                "protocol": "mvp-observation-registration-v1",
                "kind": "RULE",
                "case_id": case,
                "purpose": purpose,
                "arm_id": arm,
                "arm_algorithm": algorithm,
                "actor_registration": self.actor,
            },
            "source": {
                "case_id": case,
                "purpose": purpose,
                "implementation_files": implementation_files,
                "risk_fixture_status": STATE,
            },
        }
        refs = {}
        for kind, body in artifacts.items():
            raw = encoded(body)
            path = self.directory / (kind + "-" + digest(raw) + ".json")
            write_new(path, raw)
            refs[kind] = {"path": str(path), "sha256": digest(raw)}
        self.bindings = {
            "experiment_run_id": str(uuid4()),
            "case_id": case,
            "arm_id": arm,
            "execution_mode": "SERVICE_INTEGRATION",
            "seed_version": "mvp-301-v6",
            "purpose": purpose,
            "user_id": str(DEMO_USER_ID),
            "isolated_db_epoch": str(epoch_id),
            **{kind + "_sha256": ref["sha256"] for kind, ref in refs.items()},
        }
        self.rule = artifacts["rule"]
        self.context = SimulationContext.parse(
            {
                "protocol": "mvp-arm-isolated-context-v1",
                "bindings": self.bindings,
                "database_host": "127.0.0.1",
                "database_port": 54329,
                "database_name": require_test_database(engine.url.database),
                "now": SEED_AS_OF.isoformat(),
            }
        )
        registry: dict[str, Any] = {
            "protocol": "mvp-arm-provider-registry-v1",
            "bindings": self.bindings,
            "database_name": self.context.database_name,
            "artifact_refs": refs,
            "actor_original_directory": str(self.directory / "actor-originals"),
        }
        registry_path = (
            REPOSITORY_ROOT
            / ".runtime/W1-experiment-run-registry"
            / self.bindings["experiment_run_id"]
            / (value_digest([case, arm]) + ".json")
        )
        write_new(registry_path, encoded(registry))
        self.registry = registry

    def context_at(self, minutes: int) -> SimulationContext:
        return SimulationContext(
            self.bindings.copy(),
            self.context.database_name,
            (SEED_AS_OF + timedelta(minutes=minutes)).isoformat(),
        )

    def prepare(self) -> dict[str, Any]:
        selector = candidate_selector(self.context, self.rule, self.opportunity)
        return record_prepare(
            self.context,
            self.provider.prepare_arm_action(
                self.context, self.request, candidate_selector=selector
            ),
        )

    def review(self, response: dict[str, Any], context: SimulationContext) -> dict[str, str]:
        payload = {
            "action_id": response["action_id"],
            "effect_hash": response["effect_hash"],
            "accepted": True,
            "user_id": str(DEMO_USER_ID),
            "exact_effect": response["effect"],
            "actor_id": self.actor["actor_id"],
            "actor_kind": self.actor["actor_kind"],
            "implementation_source_ref": self.actor["implementation_source_ref"],
            "occurred_at": context.now,
        }
        original = {
            "protocol": "mvp-raw-observation-v1",
            "kind": "EXACT_EFFECT_REVIEW",
            "bindings": self.bindings,
            "payload": {"review": payload},
        }
        raw = encoded(original)
        write_new(Path(self.registry["actor_original_directory"]) / (digest(raw) + ".json"), raw)
        return {
            "artifact_sha256": digest(raw),
            "json_pointer": "/payload/review",
            "value_sha256": value_digest(payload),
        }


def no_money_rows(engine: Engine) -> None:
    with Session(engine) as session:
        for model in (ActionPlan, BankOperation, ActionReceipt, ActionResourceReservation):
            assert list(session.scalars(select(model).where(model.user_id == DEMO_USER_ID))) == []


def test_none_candidate_is_a_real_no_candidate_with_no_p_fallback(boundary_engine: Engine) -> None:
    reg = Registration(boundary_engine, amount=0)
    before = snapshot(boundary_engine)
    result = reg.prepare()
    assert result["outcome"] == "NO_CANDIDATE" and result["response"] is None
    assert result["execution_mode"] is None and result["financial_effect_evidence"] is False
    no_money_rows(boundary_engine)
    assert snapshot(boundary_engine) == before


def test_b1_original_raw_amount_changes_new_effect_without_relabelling_p(
    boundary_engine: Engine,
) -> None:
    reg = Registration(boundary_engine, amount=10000)
    with Session(boundary_engine) as session:
        original_p = preview_asset_allocation(
            session, DEMO_USER_ID, reg.policy_id, SEED_AS_OF
        ).allocation
    result = reg.prepare()
    assert result["outcome"] == "PREPARED", result
    effect = result["response"]["effect"]
    assert effect["amount_cents"] == 10000 != original_p.suggested_cents
    assert effect["policy_version_id"] == str(reg.version_id)
    assert effect["product_id"] == str(original_p.selected_product_id)
    with Session(boundary_engine) as session:
        actual = execution.get_action(
            session, DEMO_USER_ID, UUID(result["response"]["action_id"]), SEED_AS_OF
        )
        assert actual.effect_hash == execution_effect_hash(actual.effect)
        assert sum(use.amount_cents for use in actual.effect.cash_uses) == 10000
        assert not list(session.scalars(select(BankOperation)))


def test_unsafe_raw_candidate_is_actual_common_rejection_and_never_a_payment(
    boundary_engine: Engine,
) -> None:
    # Frozen B2 no-obligation baseline proposes all actual cash; normal reserve gateway remains.
    reg = Registration(boundary_engine, "B2")
    before = snapshot(boundary_engine)
    result = reg.prepare()
    assert result["outcome"] == "COMMON_GATEWAY_REJECTED" and result["response"] is None
    assert result["unsafe_candidate_status"] == "NOT_MEASURED"
    no_money_rows(boundary_engine)
    assert snapshot(boundary_engine) == before


def test_existing_effect_replay_never_calls_new_candidate_or_changes_hash(
    boundary_engine: Engine,
) -> None:
    reg = Registration(boundary_engine)
    first = reg.prepare()
    assert first["outcome"] == "PREPARED"
    before = snapshot(boundary_engine)
    later = reg.context_at(1)
    second = record_prepare(
        later,
        reg.provider.prepare_arm_action(
            later,
            reg.request,
            candidate_selector=candidate_selector(later, reg.rule, reg.opportunity),
        ),
    )
    assert second["response"]["action_id"] == first["response"]["action_id"]
    assert second["response"]["effect_hash"] == first["response"]["effect_hash"]
    capture = json.loads(
        Path(
            second["original_capture"]["original_paths"][second["original_artifact_sha256"]]
        ).read_text(encoding="utf-8")
    )
    candidate_stage = capture["payload"]["pipeline"]["candidate_before_new_effect"]["original_ref"]
    stage_original = json.loads(
        Path(
            second["original_capture"]["original_paths"][candidate_stage["artifact_sha256"]]
        ).read_text(encoding="utf-8")
    )
    assert stage_original["payload"]["stage"]["stage_state"] == "NOT_OBSERVED_OR_NOT_REACHED"
    assert snapshot(boundary_engine) == before


def test_wrong_actual_epoch_refuses_before_financial_action(boundary_engine: Engine) -> None:
    reg = Registration(boundary_engine)
    changed = reg.bindings.copy()
    changed["experiment_run_id"] = str(uuid4())
    changed["isolated_db_epoch"] = str(uuid4())
    wrong = SimulationContext(changed, reg.context.database_name, reg.context.now)
    # A fresh explicitly invalid risk registration reaches the actual SQL epoch check;
    # do not edit the prior source/inputs/registry to manufacture a mismatch.
    invalid_registry = {**reg.registry, "bindings": changed}
    path = (
        REPOSITORY_ROOT
        / ".runtime/W1-experiment-run-registry"
        / changed["experiment_run_id"]
        / (value_digest([changed["case_id"], changed["arm_id"]]) + ".json")
    )
    write_new(path, encoded(invalid_registry))
    before = snapshot(boundary_engine)
    with pytest.raises(
        reg.provider.ProviderNotImplemented, match="Actual simulated user/open epoch differs"
    ):
        reg.provider.verify_simulation_context(wrong)
    assert snapshot(boundary_engine) == before


def test_raw_callback_cannot_relabel_p_as_b1(boundary_engine: Engine) -> None:
    reg = Registration(boundary_engine)
    before = snapshot(boundary_engine)
    with pytest.raises(
        reg.provider.ProviderNotImplemented, match="original frozen independent selector"
    ):
        reg.provider.prepare_arm_action(
            reg.context, reg.request, candidate_selector=lambda _view: None
        )
    no_money_rows(boundary_engine)
    assert snapshot(boundary_engine) == before


def test_current_policy_revocation_cannot_be_cached_across_prepare_execute(
    boundary_engine: Engine,
) -> None:
    reg = Registration(boundary_engine)
    result = reg.prepare()
    assert result["outcome"] == "PREPARED"
    later = reg.context_at(1)
    with Session(boundary_engine) as session, session.begin():
        revoke_policy(
            session,
            DEMO_USER_ID,
            reg.policy_id,
            reg.version_id,
            SEED_AS_OF + timedelta(minutes=1),
        )
    with pytest.raises(PolicyLifecycleError):
        execution.execute_action(
            boundary_engine,
            DEMO_USER_ID,
            UUID(result["response"]["action_id"]),
            SEED_AS_OF + timedelta(minutes=1),
        )
    with Session(boundary_engine) as session:
        assert list(session.scalars(select(BankOperation))) == []
        action = session.get(ActionPlan, UUID(result["response"]["action_id"]))
        assert (
            action is not None
            and action.request["execution"]["effect_hash"] == result["response"]["effect_hash"]
        )
    assert later.bindings == reg.context.bindings


def test_b3_actual_original_confirmation_binds_registered_actor_and_effect(
    boundary_engine: Engine,
) -> None:
    reg = Registration(boundary_engine, "B3")
    prepared = reg.prepare()
    assert prepared["outcome"] == "PREPARED"
    response, later = prepared["response"], reg.context_at(1)
    before_cash = {
        row["id"]: row["balance_cents"] for row in json.loads(snapshot(boundary_engine))["accounts"]
    }
    review = reg.review(response, later)
    result = record_confirmation(
        later,
        reg.provider.confirm_arm_action(
            later, response["action_id"], response["effect_hash"], review
        ),
        response["action_id"],
        response["effect_hash"],
    )
    assert result["response"]["effect_hash"] == response["effect_hash"]
    with Session(boundary_engine) as session:
        originals = list(
            session.scalars(
                select(EvidenceItem).where(EvidenceItem.source_type == "USER_ACTION_CONFIRMATION")
            )
        )
        assert len(originals) == 1 and originals[0].content["action_id"] == response["action_id"]
        assert originals[0].content["effect_hash"] == response["effect_hash"]
        assert originals[0].content["accepted"] is True
        assert list(session.scalars(select(BankOperation))) == []
    after_cash = {
        row["id"]: row["balance_cents"] for row in json.loads(snapshot(boundary_engine))["accounts"]
    }
    assert after_cash == before_cash


def test_original_bank_commit_response_loss_stays_unknown_with_same_hash(
    boundary_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = Registration(boundary_engine, "P")
    prepared = reg.prepare()
    assert prepared["outcome"] == "PREPARED"
    identity = UUID(prepared["response"]["action_id"])
    digest_before = prepared["response"]["effect_hash"]

    def lost_after_original_bank_commit(engine: Engine, user: UUID, action: UUID, now: Any) -> Any:
        result = process_operation(engine, user, action, now)
        assert result.action_id == action
        raise ConnectionError("ROOT_RISK_ONLY_LOSS_AFTER_REAL_BANK_COMMIT")

    monkeypatch.setattr(execution, "process_operation", lost_after_original_bank_commit)
    later = reg.context_at(1)
    capture = reg.provider.execute_arm_action(later, str(identity))
    result = record_execution(later, capture, str(identity), digest_before)
    assert result["action_status"] == "UNKNOWN" and result["censored"] is True
    assert result["economic_execution_verified"] is False
    with Session(boundary_engine) as session:
        banks = list(
            session.scalars(select(BankOperation).where(BankOperation.action_plan_id == identity))
        )
        assert len(banks) == 1 and banks[0].status == "SETTLED"
        assert banks[0].request["effect_hash"] == digest_before
        assert (
            list(
                session.scalars(
                    select(ActionReceipt).where(ActionReceipt.action_plan_id == identity)
                )
            )
            == []
        )
        assert list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id == banks[0].id)
            )
        )
        assert list(
            session.scalars(
                select(ActionResourceReservation).where(
                    ActionResourceReservation.action_plan_id == identity
                )
            )
        )


def test_same_original_guard_excludes_reset_during_bank_phase(
    boundary_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = Registration(boundary_engine, "P")
    prepared = reg.prepare()
    assert prepared["outcome"] == "PREPARED"
    observations = []

    def original_bank_with_reset_probe(engine: Engine, user: UUID, action: UUID, now: Any) -> Any:
        # A separate connection cannot acquire the original reset-exclusive lock.
        with engine.connect() as connection:
            locked = connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": gate_key(user)}
            )
            observations.append(locked)
            assert locked is False
        return process_operation(engine, user, action, now)

    monkeypatch.setattr(execution, "process_operation", original_bank_with_reset_probe)
    capture = reg.provider.execute_arm_action(reg.context_at(1), prepared["response"]["action_id"])
    result = record_execution(
        reg.context_at(1),
        capture,
        prepared["response"]["action_id"],
        prepared["response"]["effect_hash"],
    )
    assert result["action_status"] == "SUCCEEDED" and observations == [False]
    with boundary_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": gate_key(DEMO_USER_ID)}
            )
            is True
        )


def test_real_funding_income_is_used_for_new_effect_without_fabricated_fragment(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        account = session.scalar(
            select(Account)
            .where(
                Account.user_id == DEMO_USER_ID,
                Account.account_type == "CASH",
                Account.balance_cents > 0,
            )
            .order_by(Account.balance_cents, Account.id)
        )
        assert account is not None
        cash_id, old_cash = account.id, account.balance_cents
    for kind, amount, counterparty in (
        ("CONSUMPTION", old_cash, "merchant"),
        ("INCOME", old_cash + 100000, "payroll"),
    ):
        key = "real-income-funding-risk-" + kind + "-" + uuid4().hex
        request = ExternalFactRequest.model_validate_json(
            json.dumps(
                {
                    "user_id": str(DEMO_USER_ID),
                    "kind": kind,
                    "account_id": str(cash_id),
                    "amount_cents": amount,
                    "counterparty_ref": counterparty,
                    "idempotency_key": key,
                    "external_ref": key,
                    "occurred_at": SEED_AS_OF.isoformat(),
                }
            )
        )
        fact = ingest_external_fact(demo_engine, DEMO_USER_ID, request, SEED_AS_OF)
        assert fact.bank_status == "SETTLED" and fact.projection_status == "PROJECTED"
    reg = Registration(demo_engine, amount=10000, cash_account_ids=[str(cash_id)])
    with Session(demo_engine) as session:
        ledger = read_income_state(session, DEMO_USER_ID, SEED_AS_OF).ledger
        available = {
            fragment.fragment_id: fragment.available_cents for fragment in ledger.fragments
        }
    prepared = reg.prepare()
    assert prepared["outcome"] == "PREPARED", prepared
    with Session(demo_engine) as session:
        actual = execution.get_action(
            session, DEMO_USER_ID, UUID(prepared["response"]["action_id"]), SEED_AS_OF
        )
        assert sum(use.amount_cents for use in actual.effect.income_uses) == 10000
        assert all(
            use.fragment_id in available and use.amount_cents <= available[use.fragment_id]
            for use in actual.effect.income_uses
        )
        assert actual.effect.income_uses == funding_income(
            session, DEMO_USER_ID, actual.effect.cash_uses, SEED_AS_OF
        )
        assert read_income_state(session, DEMO_USER_ID, SEED_AS_OF).ledger == ledger
        assert list(session.scalars(select(BankOperation))) == []


def test_original_guard_is_not_an_authorization_cache(boundary_engine: Engine) -> None:
    reg = Registration(boundary_engine)
    with audit_command_guard(boundary_engine, DEMO_USER_ID):
        reg.provider.verify_simulation_context(reg.context)
        # No fabricated user/status mutation: query the exact actual original twice.
        with Session(boundary_engine) as session:
            user = session.get(User, DEMO_USER_ID)
            assert user is not None and row_copy(user)["is_simulated"] is True
        reg.provider.verify_simulation_context(reg.context_at(1))
    no_money_rows(boundary_engine)


def test_real_bank_commit_projection_failure_preserves_original_unknown(
    boundary_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = Registration(boundary_engine, "P")
    prepared = reg.prepare()
    assert prepared["outcome"] == "PREPARED"
    action = UUID(prepared["response"]["action_id"])
    effect_hash = prepared["response"]["effect_hash"]
    before_cash = {
        row["id"]: row["balance_cents"] for row in json.loads(snapshot(boundary_engine))["accounts"]
    }

    def fail_original_projection(session: Session, operation: Any, now: Any) -> Any:
        # Actual phase-2 independent transaction exists before phase 3 fails.
        with Session(boundary_engine) as independent:
            committed = independent.get(BankOperation, operation.id)
            assert committed is not None and committed.status == "SETTLED"
            assert committed.request["effect_hash"] == effect_hash
        raise RuntimeError("ROOT_RISK_ONLY_ORIGINAL_PROJECTION_FAILURE_AFTER_BANK_COMMIT")

    monkeypatch.setattr(execution, "project_execution", fail_original_projection)
    later = reg.context_at(1)
    capture = reg.provider.execute_arm_action(later, str(action))
    result = record_execution(later, capture, str(action), effect_hash)
    assert result["action_status"] == "UNKNOWN" and result["censored"] is True
    with Session(boundary_engine) as session:
        banks = list(
            session.scalars(select(BankOperation).where(BankOperation.action_plan_id == action))
        )
        assert len(banks) == 1 and banks[0].status == "SETTLED"
        assert banks[0].request["effect_hash"] == effect_hash
        assert not list(
            session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action))
        )
        assert list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id == banks[0].id)
            )
        )
        assert list(
            session.scalars(
                select(ActionResourceReservation).where(
                    ActionResourceReservation.action_plan_id == action
                )
            )
        )
    after_cash = {
        row["id"]: row["balance_cents"] for row in json.loads(snapshot(boundary_engine))["accounts"]
    }
    assert after_cash == before_cash
