"""Synthetic service fixtures only; no database, bank truth or economic effect proof.

The immutable portfolio reader, child identity verifier, original claim constructor
and zero-bank command/release helpers run unchanged. Audit/trace storage and final
bank receipt verification are injected protocol boundaries, explicitly not PG.
"""

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock
from uuid import UUID, uuid5

import pytest
from app.db.full_models import (
    FullAssetExecutionBatch,
    FullAssetExecutionConsent,
    FullAssetExecutionPortfolio,
    FullPolicy,
    FullPolicyVersion,
)
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AuditEpoch,
    BankOperation,
    PolicyVersion,
    SimulatedBankPosting,
    SimulatedBankRedemption,
)
from app.domain.decision_trace import build_trace
from app.domain.execution_types import ExecutionContext
from app.domain.full_asset_execution import FullAssetFrozenPortfolio, build_frozen_portfolio
from app.domain.income_ledger import IncomeLedger, IncomeReservation
from app.domain.policy_configuration import configuration_hash
from app.services import full_asset_action_rechecks as service
from app.services import full_asset_execution_store as store
from app.services.action_contracts import ActionResponse
from app.services.decision_trace import DecisionTraceResponse
from app.services.execution import _claims
from app.services.full_asset_execution_store import batch_binding
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_asset_execution import literal_basis
from sqlalchemy import Select
from sqlalchemy.orm import Session
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList, Grouping


def _matches(expression: Any, row: Any) -> bool:
    """Apply actual SELECT predicates to fixture rows, including owner OR branches."""
    if expression is None:
        return True
    if isinstance(expression, Grouping):
        return _matches(expression.element, row)
    if isinstance(expression, BooleanClauseList):
        results = [_matches(item, row) for item in expression.clauses]
        if expression.operator is operators.and_:
            return all(results)
        if expression.operator is operators.or_:
            return any(results)
    if isinstance(expression, BinaryExpression) and isinstance(expression.right, BindParameter):
        key = expression.left.key
        assert isinstance(key, str)
        left = getattr(row, key)
        right = expression.right.value
        if expression.operator is operators.eq:
            return bool(left == right)
        if expression.operator is operators.in_op:
            assert isinstance(right, (list, tuple, set))
            return left in right
    raise AssertionError(f"Unimplemented fixture SELECT predicate: {expression}")


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def __iter__(self) -> Iterator[Any]:
        return iter(self.rows)

    def all(self) -> list[Any]:
        return self.rows


class Harness:
    def __init__(
        self,
        frozen: FullAssetFrozenPortfolio,
        context: ExecutionContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.frozen, self.context = frozen, context
        self.now = frozen.prepared_at
        self.command_id = UUID(int=990)
        self.session = Mock(spec=Session)
        self.tables: dict[type[Any], list[Any]] = {}
        self.queries: list[Select[Any]] = []
        self.events: list[dict[str, Any]] = []
        self.parent = FullAssetExecutionPortfolio(
            id=frozen.portfolio_id,
            user_id=frozen.user_id,
            created_at=self.now,
            epoch_id=frozen.epoch_id,
            idempotency_key=frozen.original_request.idempotency_key,
            request=frozen.original_request.model_dump(mode="json"),
            request_hash=frozen.client_request_hash,
            portfolio=frozen.model_dump(mode="json"),
            portfolio_hash=frozen.portfolio_hash,
            expires_at=frozen.expires_at,
        )
        self.policy = FullPolicy(
            id=frozen.original_request.full_policy_id,
            user_id=frozen.user_id,
            created_at=self.now,
            epoch_id=frozen.epoch_id,
            template_name="AssetAuthorizationPolicy",
            dsl_version="FULL_V1",
            name="PURE_ONLY",
            status="REVOKED",
            updated_at=self.now,
        )
        self.version = FullPolicyVersion(
            id=frozen.original_request.expected_full_policy_version_id,
            user_id=frozen.user_id,
            created_at=self.now,
            policy_id=self.policy.id,
            version_number=1,
            configuration=frozen.full_configuration.model_dump(mode="json"),
            content_hash=frozen.full_policy_content_hash,
            previous_hash=None,
            confirmed_at=self.now,
            valid_from=self.now,
            valid_until=None,
            confirmation={},
            evidence_ids=[],
            impact_analysis={},
        )
        self.epoch = AuditEpoch(
            id=frozen.epoch_id, user_id=frozen.user_id, created_at=self.now, status="OPEN"
        )
        self.bindings = [
            FullAssetExecutionBatch(
                id=uuid5(frozen.portfolio_id, f"binding:{b.batch_number}"),
                user_id=frozen.user_id,
                created_at=self.now,
                portfolio_id=frozen.portfolio_id,
                epoch_id=frozen.epoch_id,
                batch_number=b.batch_number,
                action_plan_id=b.action_id,
                bank_idempotency_key=b.bank_idempotency_key,
                command=b.command.model_dump(mode="json"),
                command_hash=configuration_hash(b.command.model_dump(mode="json")),
                catalogue_version_id=b.catalogue.catalogue_version_id,
                product_record_hash=b.catalogue.product_record_hash,
            )
            for b in frozen.batches
        ]
        self.actions: list[ActionPlan] = []
        self.traces: dict[UUID, DecisionTraceResponse] = {}
        for batch in frozen.batches:
            effect = batch.command.effect
            request = {
                "execution": batch.command.model_dump(mode="json"),
                "full_asset_execution": batch_binding(frozen, batch.batch_number).model_dump(
                    mode="json"
                ),
            }
            action = ActionPlan(
                id=batch.action_id,
                user_id=frozen.user_id,
                created_at=self.now,
                decision_run_id=uuid5(batch.action_id, "decision"),
                policy_version_id=effect.policy_version_id,
                source_account_id=effect.cash_uses[0].account_id,
                destination_account_id=None,
                goal_id=effect.goal_id,
                product_id=effect.product_id,
                position_id=None,
                action_type="ASSET_PURCHASE",
                amount_cents=effect.amount_cents,
                autonomy_level="ASK_ONCE",
                status="PLANNED",
                idempotency_key=batch.bank_idempotency_key,
                request=request,
                request_hash=configuration_hash(request),
                authorized_at=None,
                expires_at=frozen.expires_at,
            )
            trace = build_trace(
                run_id=action.decision_run_id,
                user_id=action.user_id,
                action_id=action.id,
                phase="PREPARE",
                as_of=self.now,
                algorithm_versions={"execution": "economic-effect-revalidation-v1"},
                inputs={
                    "effect": effect.model_dump(mode="json"),
                    "execution_context": context.model_dump(mode="json"),
                    "action_request": request,
                },
                outcome={"fixture_kind": "SYNTHETIC_PROTOCOL_BOUNDARY_ONLY"},
            )
            self.traces[action.decision_run_id] = DecisionTraceResponse(
                user_id=action.user_id,
                run_id=action.decision_run_id,
                as_of=self.now,
                read_at=self.now,
                completeness="COMPLETE",
                trace=trace,
                current_references=[],
                actions=[],
                children=[],
                audit_chain_status="VALID",
            )
            self.actions.append(action)
        mvp = context.versions[0]
        self.tables = {
            FullAssetExecutionPortfolio: [self.parent],
            FullAssetExecutionBatch: self.bindings,
            FullAssetExecutionConsent: [],
            FullPolicy: [self.policy],
            FullPolicyVersion: [self.version],
            PolicyVersion: [PolicyVersion(id=mvp.version_id, configuration=mvp.configuration)],
            AuditEpoch: [self.epoch],
            ActionPlan: self.actions,
            BankOperation: [],
            ActionReceipt: [],
            SimulatedBankPosting: [],
            SimulatedBankRedemption: [],
            ActionResourceReservation: [],
        }
        self.ledger = IncomeLedger(
            user_id=frozen.user_id,
            as_of=self.now,
            scope_account_ids=tuple(a.account_id for a in context.snapshot.cash_accounts),
            origins=(),
            fragments=(),
        )
        self.session.get.side_effect = self.get
        self.session.scalars.side_effect = self.scalars
        self.session.scalar.side_effect = self.scalar
        monkeypatch.setattr(service, "_user", Mock())
        monkeypatch.setattr(service, "_versions", lambda *_: self.tables[FullPolicyVersion])
        monkeypatch.setattr(service, "current_audit_epoch", lambda *_: self.epoch)
        self.audit = Mock(return_value=SimpleNamespace(status="VALID"))
        monkeypatch.setattr(service, "verify_audit_chain", self.audit)
        # Synthetic storage boundaries are never represented as actual verified PG.
        monkeypatch.setattr(store, "get_action", lambda *_: Mock(spec=ActionResponse))
        monkeypatch.setattr(store, "get_decision_trace", self.trace)
        monkeypatch.setattr(service, "get_decision_trace", self.trace)
        monkeypatch.setattr(
            service, "read_income_state", lambda *_: SimpleNamespace(ledger=self.ledger)
        )
        self.receipt_verifier = Mock()
        monkeypatch.setattr(service, "verify_execution_receipt", self.receipt_verifier)
        monkeypatch.setattr(service, "record_action_transition", self.record)
        monkeypatch.setattr(service, "_epochs", Mock())
        self.exposure = Mock()
        monkeypatch.setattr(service, "refresh_execution_exposure", self.exposure)

    def get(self, model: type[Any], identifier: UUID) -> Any:
        return next((row for row in self.tables.get(model, []) if row.id == identifier), None)

    def scalars(self, query: Select[Any]) -> _Rows:
        self.queries.append(query)
        model = query.column_descriptions[0]["entity"]
        return _Rows(
            [row for row in self.tables.get(model, []) if _matches(query.whereclause, row)]
        )

    def scalar(self, query: Select[Any]) -> Any:
        rows = self.scalars(query).all()
        return rows[0] if rows else None

    def trace(self, _: Session, __: UUID, identifier: UUID, ___: Any) -> DecisionTraceResponse:
        return self.traces[identifier]

    def record(self, _: Session, action: ActionPlan, before: str, __: Any, **data: Any) -> None:
        self.events.append(
            {"action_id": action.id, "before": before, "after": action.status, **data}
        )

    def run(self) -> tuple[list[UUID], list[UUID]]:
        result = service.recheck_full_asset_actions(
            cast(Session, self.session),
            self.frozen.user_id,
            self.policy.id,
            self.frozen.epoch_id,
            self.now,
            self.command_id,
        )
        self.session.commit.assert_not_called()
        self.session.add.assert_not_called()
        self.session.delete.assert_not_called()
        return result

    def claims(self, status: str = "RESERVED") -> list[ActionResourceReservation]:
        action, effect = self.actions[0], self.frozen.batches[0].command.effect
        values = _claims(cast(Session, self.session), effect, self.context)
        rows = [
            ActionResourceReservation(
                id=uuid5(action.id, f"resource:{value.resource_kind}:{value.resource_key}"),
                user_id=action.user_id,
                created_at=self.now,
                action_plan_id=action.id,
                resource_kind=value.resource_kind,
                resource_key=value.resource_key,
                amount_cents=value.amount_cents,
                status=status,
                resolved_at=None if status == "RESERVED" else self.now,
            )
            for value in values
        ]
        self.tables[ActionResourceReservation] = rows
        return rows

    def bank(self, status: str = "SETTLED") -> BankOperation:
        action, command = self.actions[0], self.frozen.batches[0].command
        row = BankOperation(
            id=action.id,
            user_id=action.user_id,
            created_at=self.now,
            action_plan_id=action.id,
            legacy_redemption_id=None,
            operation_type="PURCHASE_ASSET",
            business_key=command.effect.business_key,
            idempotency_key=action.idempotency_key,
            request=command.model_dump(mode="json"),
            request_hash=configuration_hash(command.model_dump(mode="json")),
            requested_at=self.now,
            available_at=self.now,
            settled_at=self.now if status == "SETTLED" else None,
            status=status,
        )
        self.tables[BankOperation].append(row)
        return row

    def settled(self) -> None:
        action, command = self.actions[0], self.frozen.batches[0].command
        self.bank()
        action.status = "SUCCEEDED"
        self.claims("CONSUMED")
        self.tables[SimulatedBankPosting] = [
            SimulatedBankPosting(
                id=UUID(int=2000 + n),
                user_id=action.user_id,
                operation_id=action.id,
                redemption_id=None,
                leg_ref=f"fixture:{n}",
            )
            for n in range(3)
        ]
        self.tables[ActionReceipt] = [
            ActionReceipt(
                id=uuid5(action.id, "receipt"),
                user_id=action.user_id,
                action_plan_id=action.id,
                status="SUCCEEDED",
                executed_cents=command.effect.amount_cents,
                response={
                    "posting_ids": [str(row.id) for row in self.tables[SimulatedBankPosting]]
                },
            )
        ]


@pytest.fixture(scope="module")
def original() -> tuple[FullAssetFrozenPortfolio, ExecutionContext]:
    body, basis = literal_basis()
    return build_frozen_portfolio(body, basis), basis.context


@pytest.fixture
def harness(
    original: tuple[FullAssetFrozenPortfolio, ExecutionContext], monkeypatch: pytest.MonkeyPatch
) -> Harness:
    return Harness(*original, monkeypatch)


def test_revoke_invalidates_exact_original_children_and_does_not_rewrite_parent(
    harness: Harness,
) -> None:
    before = configuration_hash(harness.parent.portfolio)
    original_hashes = [
        (a.request_hash, a.idempotency_key, a.policy_version_id) for a in harness.actions
    ]
    invalidated, inflight = harness.run()
    assert invalidated == sorted((a.id for a in harness.actions), key=str) and inflight == []
    assert all(a.status == "INVALIDATED" for a in harness.actions)
    assert before == configuration_hash(harness.parent.portfolio)
    assert harness.parent.portfolio_hash == harness.frozen.portfolio_hash
    assert original_hashes == [
        (a.request_hash, a.idempotency_key, a.policy_version_id) for a in harness.actions
    ]
    assert all(a.policy_version_id != harness.version.id for a in harness.actions)
    assert len(harness.events) == 2
    assert all(e["details"]["no_effect_status"] == "CONFIRMED" for e in harness.events)
    assert all(
        e["cause_ref"] == f"full-policy-command:{harness.command_id}" for e in harness.events
    )
    declarations = harness.exposure.call_args.kwargs["declarations"]
    assert set(declarations) == set(invalidated)
    assert all(row["state"] == "NO_EFFECT" for row in declarations.values())


def test_unchanged_scope_retains_planned_original_without_writes(harness: Harness) -> None:
    harness.policy.status = "ACTIVE"
    assert harness.run() == ([], [])
    assert all(a.status == "PLANNED" for a in harness.actions)
    assert harness.events == []
    harness.session.flush.assert_not_called()
    harness.exposure.assert_not_called()


def test_new_full_uuid_version_invalidates_old_children_even_if_config_hash_equal(
    harness: Harness,
) -> None:
    harness.policy.status = "ACTIVE"
    old = harness.version
    new = FullPolicyVersion(
        id=UUID(int=999),
        policy_id=old.policy_id,
        user_id=old.user_id,
        version_number=2,
        content_hash=old.content_hash,
        valid_from=old.valid_from,
        valid_until=None,
    )
    harness.tables[FullPolicyVersion].append(new)
    assert harness.run()[0] == sorted((a.id for a in harness.actions), key=str)
    assert all(a.policy_version_id != new.id for a in harness.actions)


@pytest.mark.parametrize("status", ["SUBMITTED", "UNKNOWN", "FAILED", "SUCCEEDED", "RECONCILED"])
def test_unproven_status_never_releases_or_declares_no_effect(
    harness: Harness, status: str
) -> None:
    for action in harness.actions:
        action.status = status
    claims = harness.claims()
    assert harness.run() == ([], sorted((a.id for a in harness.actions), key=str))
    assert all(row.status == "RESERVED" for row in claims)
    assert harness.events == []


@pytest.mark.parametrize("status", ["REJECTED", "ACCEPTED", "UNKNOWN", "SETTLED"])
def test_any_bank_original_including_rejected_is_retained(harness: Harness, status: str) -> None:
    bank = harness.bank(status)
    bank_before = (bank.status, bank.request_hash, bank.idempotency_key)
    claims = harness.claims()
    assert harness.run() == ([harness.actions[1].id], [harness.actions[0].id])
    assert bank_before == (bank.status, bank.request_hash, bank.idempotency_key)
    assert all(row.status == "RESERVED" for row in claims)
    assert harness.actions[0].status == "PLANNED"


@pytest.mark.parametrize("kind", ["receipt", "post", "redemption", "alien-bank", "redemption-post"])
def test_complete_economic_denominators_do_not_hide_alien_or_orphan_rows(
    harness: Harness, kind: str
) -> None:
    action = harness.actions[0]
    alien = UUID(int=333)
    if kind == "receipt":
        harness.tables[ActionReceipt] = [
            ActionReceipt(id=UUID(int=334), user_id=alien, action_plan_id=action.id)
        ]
    elif kind == "post":
        harness.tables[SimulatedBankPosting] = [
            SimulatedBankPosting(
                id=UUID(int=334), user_id=alien, operation_id=action.id, redemption_id=None
            )
        ]
    elif kind == "redemption":
        harness.tables[SimulatedBankRedemption] = [
            SimulatedBankRedemption(id=UUID(int=334), user_id=alien, action_plan_id=action.id)
        ]
    elif kind == "alien-bank":
        harness.bank("REJECTED").user_id = alien
    else:
        harness.tables[SimulatedBankPosting] = [
            SimulatedBankPosting(
                id=UUID(int=334), user_id=alien, operation_id=None, redemption_id=action.id
            )
        ]
    assert harness.run() == ([harness.actions[1].id], [action.id])
    assert action.status == "PLANNED"


def test_verified_unsubmitted_original_claims_use_old_release_helper(harness: Harness) -> None:
    harness.actions[0].status = "AUTHORIZED"
    claims = harness.claims()
    invalidated, inflight = harness.run()
    assert len(invalidated) == 2 and inflight == []
    assert all(row.status == "RELEASED" and row.resolved_at == harness.now for row in claims)
    assert harness.events[0]["details"]["released_claim_ids"] == sorted(
        str(row.id) for row in claims
    )


@pytest.mark.parametrize(
    "risk", ["owner", "uuid", "amount", "partial", "consumed", "extra", "time"]
)
def test_incomplete_or_unbound_claims_remain_inflight(harness: Harness, risk: str) -> None:
    rows = harness.claims()
    if risk == "owner":
        rows[0].user_id = UUID(int=3)
    elif risk == "uuid":
        rows[0].id = UUID(int=3)
    elif risk == "amount":
        rows[0].amount_cents += 1
    elif risk == "partial":
        rows.pop()
    elif risk == "consumed":
        for row in rows:
            row.status, row.resolved_at = "CONSUMED", harness.now
    elif risk == "extra":
        rows.append(
            ActionResourceReservation(
                id=UUID(int=3),
                user_id=harness.frozen.user_id,
                action_plan_id=harness.actions[0].id,
                resource_kind="POSITION",
                resource_key="unknown",
                amount_cents=1,
                status="RESERVED",
                created_at=harness.now,
            )
        )
    else:
        rows[0].created_at = harness.now + timedelta(seconds=1)
    assert harness.run() == ([harness.actions[1].id], [harness.actions[0].id])
    assert harness.actions[0].status == "PLANNED"


@pytest.mark.parametrize(
    "risk",
    [
        "raw-request-hash",
        "raw-parent",
        "batch-hash",
        "batch-missing",
        "marker",
        "action-money",
        "action-owner",
        "missing-action",
    ],
)
def test_original_whole_and_action_anomalies_are_never_labeled_zero_effect(
    harness: Harness, risk: str
) -> None:
    if risk == "raw-request-hash":
        harness.parent.request_hash = "0" * 64
    elif risk == "raw-parent":
        harness.parent.portfolio = {**harness.parent.portfolio, "total_purchase_cents": 1}
    elif risk == "batch-hash":
        harness.bindings[0].command_hash = "0" * 64
    elif risk == "batch-missing":
        harness.bindings.pop()
    elif risk == "marker":
        harness.actions[0].request = {"execution": harness.actions[0].request["execution"]}
        harness.actions[0].request_hash = configuration_hash(harness.actions[0].request)
    elif risk == "action-money":
        harness.actions[0].amount_cents += 1
    elif risk == "action-owner":
        harness.actions[0].user_id = UUID(int=4)
    else:
        harness.tables[ActionPlan] = harness.actions[1:]
    invalidated, inflight = harness.run()
    assert harness.frozen.batches[0].action_id in inflight
    assert harness.frozen.batches[0].action_id not in invalidated
    assert harness.actions[0].status == "PLANNED"


@pytest.mark.parametrize("risk", ["missing-trace", "audit", "phase", "effect", "raw-request"])
def test_original_prepare_trace_is_not_a_success_string(harness: Harness, risk: str) -> None:
    action = harness.actions[0]
    original = harness.traces[action.decision_run_id]
    assert original.trace is not None
    if risk == "missing-trace":
        response = original.model_copy(update={"completeness": "MISSING", "trace": None})
    elif risk == "audit":
        response = original.model_copy(update={"audit_chain_status": "INVALID"})
    else:
        content = original.trace.model_dump(exclude={"input_hash", "trace_hash"})
        if risk == "phase":
            content["phase"] = "CONFIRM"
        elif risk == "effect":
            content["inputs"] = {**content["inputs"], "effect": {}}
        else:
            content["inputs"] = {**content["inputs"], "action_request": {}}
        response = original.model_copy(update={"trace": build_trace(**content)})
    harness.traces[action.decision_run_id] = response
    assert harness.run() == ([harness.actions[1].id], [action.id])
    assert action.status == "PLANNED"


def test_original_settlement_requires_strict_receipt_verifier_without_current_permission(
    harness: Harness,
) -> None:
    harness.settled()
    assert harness.policy.status == "REVOKED"
    assert harness.run() == ([harness.actions[1].id], [])
    harness.receipt_verifier.assert_called_once_with(
        harness.session,
        harness.tables[BankOperation][0],
        harness.tables[ActionReceipt][0],
        harness.now,
    )
    assert harness.actions[0].status == "SUCCEEDED"
    assert all(row.status == "CONSUMED" for row in harness.tables[ActionResourceReservation])


@pytest.mark.parametrize(
    "risk",
    ["receipt-rejected", "duplicate-bank", "receipt-missing", "alien-post", "claims-missing"],
)
def test_settled_label_without_exact_all_originals_is_unresolved(
    harness: Harness, risk: str
) -> None:
    harness.settled()
    if risk == "receipt-rejected":
        harness.receipt_verifier.side_effect = PolicyLifecycleError(
            "BANK_RECONCILIATION_REQUIRED", "原两腿未验真", 409
        )
    elif risk == "duplicate-bank":
        harness.bank()
    elif risk == "receipt-missing":
        harness.tables[ActionReceipt] = []
    elif risk == "alien-post":
        harness.tables[SimulatedBankPosting][0].user_id = UUID(int=4)
    else:
        harness.tables[ActionResourceReservation] = []
    assert harness.run() == ([harness.actions[1].id], [harness.actions[0].id])
    assert harness.actions[0].status == "SUCCEEDED"


def test_global_invalid_audit_retains_all_keys_and_claims(harness: Harness) -> None:
    claims = harness.claims()
    harness.audit.return_value.status = "INVALID"
    assert harness.run() == ([], sorted((a.id for a in harness.actions), key=str))
    assert all(row.status == "RESERVED" for row in claims)
    harness.audit.assert_called_once_with(
        harness.session, harness.frozen.user_id, epoch_id=harness.frozen.epoch_id, mode="EXACT"
    )
    assert harness.events == []


def test_orphan_persistent_binding_is_retained_not_ignored(harness: Harness) -> None:
    orphan = FullAssetExecutionBatch(
        id=UUID(int=9990),
        user_id=harness.frozen.user_id,
        epoch_id=harness.frozen.epoch_id,
        portfolio_id=UUID(int=9991),
        action_plan_id=UUID(int=9992),
    )
    harness.bindings.append(orphan)
    assert harness.run() == (
        sorted((a.id for a in harness.actions), key=str),
        [orphan.action_plan_id],
    )


def test_missing_batch_does_not_shrink_original_frozen_action_denominator(
    harness: Harness,
) -> None:
    harness.bindings.pop()
    assert harness.run() == ([], sorted((a.id for a in harness.actions), key=str))
    assert all(a.status == "PLANNED" for a in harness.actions)
    assert harness.events == []


def test_no_effect_audit_failure_propagates_to_callers_existing_rollback(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Mock(
        side_effect=PolicyLifecycleError("AUDIT_REFERENCE_ERROR", "实际审计写失败", 409)
    )
    monkeypatch.setattr(service, "record_action_transition", recorder)
    with pytest.raises(PolicyLifecycleError, match="实际审计写失败"):
        harness.run()
    # The in-memory fixture has no rollback; this proves propagation, not a PG rollback.
    harness.exposure.assert_not_called()


@pytest.mark.parametrize("risk", ["wrong-epoch", "wrong-owner", "wrong-family"])
def test_actual_policy_scope_cannot_be_supplied_from_another_identity(
    harness: Harness, risk: str
) -> None:
    if risk == "wrong-epoch":
        harness.policy.epoch_id = UUID(int=8)
    elif risk == "wrong-owner":
        harness.policy.user_id = UUID(int=8)
    else:
        harness.policy.template_name = "DatedExpensePolicy"
    with pytest.raises(PolicyLifecycleError, match="实际当前原策略"):
        harness.run()
    assert harness.events == []


def test_missing_income_original_does_not_release_claims(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = harness.claims()
    reader = Mock(
        side_effect=PolicyLifecycleError("MISSING_NEW_FUNDS_LEDGER", "原收入账本缺失", 409)
    )
    monkeypatch.setattr(service, "read_income_state", reader)
    assert harness.run() == ([], sorted((a.id for a in harness.actions), key=str))
    assert all(row.status == "RESERVED" for row in claims)


def test_spend_committed_income_state_maps_to_consumed_resources_not_reserved(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = harness.frozen.batches[0].command
    action = harness.actions[0]
    original = harness.ledger
    harness.ledger = original.model_copy(
        update={
            "reservations": (
                IncomeReservation(
                    action_id=action.id, operation="SPEND", uses=(), state="COMMITTED"
                ),
            )
        }
    )
    monkeypatch.setattr(service, "_reservation_matches", Mock())
    rows = service._original_rows(cast(Session, harness.session), action)
    harness.claims("CONSUMED")
    rows = service._original_rows(cast(Session, harness.session), action)
    assert (
        service._verified_claim_state(
            cast(Session, harness.session), action, command, rows, harness.now
        )
        == "CONSUMED"
    )
