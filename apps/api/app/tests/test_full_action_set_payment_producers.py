"""Synthetic exact originals and hand-calculated 300-cent payment, not PG evidence."""

import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid5

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_action_set_payment_producers as api
from app.domain.autonomy_types import AuthorityAssessment, AutonomyFacts, PayeeAssessment
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import TraceEvidence, TracePolicy
from app.domain.execution import revalidate_execution
from app.domain.full_action_set_boundary import (
    ActionSetInput,
    CandidateInput,
    unsupported_producers,
)
from app.domain.full_action_set_boundary_actual import (
    REQUIRED_TABLES,
    ActualActionSetInput,
    actual_producer_keys,
    derive_actual_action_set,
)
from app.domain.full_action_set_payment_producers import (
    ALGORITHM,
    PeriodicActionSetInput,
    PeriodicOriginalCommand,
    PeriodicPaymentProducerInput,
    derive_periodic_payment_producers,
)
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_payment_permissions import (
    SOURCE,
    PaymentRelationScope,
    account_payment_identity,
    payment_command_identity,
    payment_scope_hash,
)
from app.domain.full_policy_configuration import PeriodicTransferPolicy
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import ExactAmount, configuration_hash
from app.services import full_action_set_payment_producers as service
from app.services.decision_recording import CAPTURE_KEY, DecisionCapture
from app.services.full_action_set_boundary_actual import ActualActionSetCapture
from app.services.full_payment_permissions import (
    PaymentCommandOriginal,
    PaymentCommandReceipt,
    PeriodicTransferProjectionBinding,
)
from app.services.full_policy_lifecycle import FullPolicyView, FullVersionView
from app.tests.test_execution_domain import (
    EVIDENCE,
    NOW,
    POLICY,
    USER,
    VERSION,
    A,
    context,
    payment,
    recurring,
)
from app.tests.test_full_action_set_boundary_actual import coverage
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

EPOCH, FULL, FULL_VERSION = UUID(int=2604), UUID(int=2606), UUID(int=2607)


def fixture(*, auto: bool = True, confirmed: bool = True) -> PeriodicActionSetInput:
    version = recurring()
    config = copy.deepcopy(version.configuration)
    config["auto_execute"] = auto
    version = version.model_copy(
        update={"configuration": config, "content_hash": configuration_hash(config)}
    )
    current = context().model_copy(
        update={"versions": [version], "requires_confirmation": not auto}
    )
    effect = payment().model_copy(update={"business_key": f"recurring:{POLICY}:2026-10"})
    raw: dict[str, list[dict[str, Any]]] = {name: [] for name in REQUIRED_TABLES}
    raw["users"] = [{"id": str(USER), "is_simulated": True}]
    raw["accounts"] = [
        dict(
            id=str(row.account_id),
            user_id=str(USER),
            account_type="CASH",
            currency="CNY",
            bank_code="ICBC",
            external_ref=str(row.account_id),
            balance_cents=row.balance_cents,
        )
        for row in current.snapshot.cash_accounts
    ]
    raw["policies"] = [{"id": str(POLICY), "user_id": str(USER), "status": "ACTIVE"}]
    raw["policy_versions"] = [
        {
            "id": str(VERSION),
            "user_id": str(USER),
            "policy_id": str(POLICY),
            "version_number": 1,
            "configuration": config,
            "content_hash": version.content_hash,
            "confirmed_at": version.confirmed_at.isoformat(),
            "valid_from": version.valid_from.isoformat(),
            "valid_until": version.valid_until.isoformat() if version.valid_until else None,
        }
    ]
    proof_content = {
        "counterparty_ref": "landlord-fixed",
        "economic_role": "CONSUMPTION",
        "fixture_tier": "SYNTHETIC_DIRECT_TEST_ONLY",
    }
    payee: dict[str, Any] = {
        "id": str(EVIDENCE),
        "user_id": str(USER),
        "source_type": "SIMULATED_BANK_TRANSACTION",
        "source_ref": "synthetic:bank-not-executed",
        "content": proof_content,
        "content_hash": configuration_hash(proof_content),
        "status": "VALID",
        "evidence_level": "BANK_CONFIRMED",
        "observed_at": NOW.isoformat(),
        "valid_from": NOW.isoformat(),
        "valid_to": None,
    }
    raw["evidence_items"] = [payee]
    account = next(row for row in raw["accounts"] if row["id"] == str(A))
    refs = [
        {
            "role": "source_account",
            "kind": "ACCOUNT",
            "id": str(A),
            "binding_hash": configuration_hash({"id": str(A), "owner": str(USER), "type": "CASH"}),
            "snapshot": copy.deepcopy(account),
        },
        {
            "role": "payee_source",
            "kind": "EVIDENCE",
            "id": str(EVIDENCE),
            "binding_hash": configuration_hash(
                {"id": str(EVIDENCE), "hash": payee["content_hash"], "payee_id": "landlord-fixed"}
            ),
            "snapshot": copy.deepcopy(payee),
        },
    ]
    full_config = PeriodicTransferPolicy.model_validate_json(
        json.dumps(
            {
                "type": "periodic_transfer",
                "payee_id": "landlord-fixed",
                "source_account_id": str(A),
                "single_action_cap_cents": 300,
                "amount_rule": {"kind": "exact", "amount_cents": 300},
                "due_day": 4,
                "prepare_days_before": config["prepare_days_before"],
                "auto_execute": auto,
                "valid_until": "2026-10-06",
            }
        )
    ).model_dump(mode="json")
    full_hash = configuration_hash(full_config)
    full_version = FullVersionView(
        version_id=FULL_VERSION,
        policy_id=FULL,
        version_number=1,
        configuration=full_config,
        content_hash=full_hash,
        previous_hash=None,
        summary="synthetic",
        confirmation={"accepted": True, "reviewed_hash": full_hash},
        confirmed_at=NOW,
        valid_from=NOW,
        valid_until=NOW + timedelta(days=2),
        change_reason="synthetic",
        evidence_ids=[EVIDENCE],
        impact_analysis={"reference_snapshots": refs},
        confirmation_evidence_status="CURRENT_EVIDENCE_MATCHED",
    )
    full = FullPolicyView(
        policy_id=FULL,
        epoch_id=EPOCH,
        template_name="PeriodicTransferPolicy",
        name="synthetic",
        status="ACTIVE",
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        reference_validation="CURRENT",
        current_version=full_version,
        updated_at=NOW,
    )
    raw["full_policies"] = [
        {
            "id": str(FULL),
            "user_id": str(USER),
            "epoch_id": str(EPOCH),
            "template_name": "PeriodicTransferPolicy",
            "status": "ACTIVE",
        }
    ]
    raw["full_policy_versions"] = [
        {
            "id": str(FULL_VERSION),
            "user_id": str(USER),
            "policy_id": str(FULL),
            "version_number": 1,
            "configuration": full_config,
            "content_hash": full_hash,
            "confirmed_at": NOW.isoformat(),
            "valid_from": NOW.isoformat(),
            "valid_until": (NOW + timedelta(days=2)).isoformat(),
            "confirmation": full_version.confirmation,
            "impact_analysis": full_version.impact_analysis,
            "previous_hash": full_version.previous_hash,
            "evidence_ids": [str(x) for x in full_version.evidence_ids],
        }
    ]
    scope = PaymentRelationScope(
        user_id=USER,
        epoch_id=EPOCH,
        full_policy_id=FULL,
        full_version_id=FULL_VERSION,
        full_configuration_hash=full_hash,
        original_policy_id=POLICY,
        original_version_id=VERSION,
        original_configuration_hash=version.content_hash,
        payee_id="landlord-fixed",
        payee_evidence_id=EVIDENCE,
        payee_evidence_hash=payee["content_hash"],
        source_account_id=A,
        source_account_identity_hash=configuration_hash(account_payment_identity(account)),
        amount_rule=ExactAmount(kind="exact", amount_cents=300),
        due_day=4,
        single_action_cap_cents=300,
        auto_execute=auto,
        timezone="UTC",
        valid_from=NOW,
        valid_until=NOW + timedelta(days=2),
    )
    principal = LocalActorPrincipal(
        user_id=USER,
        role="USER",
        session_id=UUID(int=2608),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )
    start_body = {
        "expected_epoch_id": str(EPOCH),
        "full_policy_id": str(FULL),
        "expected_full_version_id": str(FULL_VERSION),
        "original_policy_id": str(POLICY),
        "expected_original_version_id": str(VERSION),
        "idempotency_key": "synthetic-start",
    }
    start_id = payment_command_identity(USER, EPOCH, "synthetic-start")
    confirm_body = {
        "start_command_id": str(start_id),
        "confirmation": {
            "expected_epoch_id": str(EPOCH),
            "reviewed_scope_hash": payment_scope_hash(scope),
            "accepted": True,
            "reason": "synthetic explicit",
            "idempotency_key": "synthetic-confirm",
        },
    }
    commands: list[PeriodicOriginalCommand] = []
    policy_copy = TracePolicy(
        id=VERSION,
        user_id=USER,
        policy_id=POLICY,
        version_number=1,
        configuration=config,
        configuration_hash=version.content_hash,
        captured_configuration_hash=version.content_hash,
        configuration_integrity="VERIFIED",
        status_at_decision="ACTIVE",
        confirmed_at=version.confirmed_at,
        valid_from=version.valid_from,
        valid_to=version.valid_until,
    )
    if confirmed:
        for kind, body, key in [
            ("START", start_body, "synthetic-start"),
            ("CONFIRM", confirm_body, "synthetic-confirm"),
        ]:
            original = PaymentCommandOriginal(
                command_id=payment_command_identity(USER, EPOCH, key),
                user_id=USER,
                epoch_id=EPOCH,
                kind=cast(Any, kind),
                idempotency_key=key,
                start_command_id=None if kind == "START" else start_id,
                original_request=cast(dict[str, Any], body),
                request_hash=configuration_hash(
                    {"user_id": str(USER), "kind": kind, "request": body}
                ),
                principal_at_command=principal,
                scope=scope,
                scope_hash=payment_scope_hash(scope),
                recorded_at=NOW,
            )
            eid = uuid5(original.command_id, "evidence")
            raw["evidence_items"].append(
                {
                    "id": str(eid),
                    "user_id": str(USER),
                    "source_type": SOURCE,
                    "source_ref": str(original.command_id),
                    "content": original.model_dump(mode="json"),
                    "content_hash": configuration_hash(original.model_dump(mode="json")),
                    "status": "VALID",
                    "evidence_level": "USER_DECLARED"
                    if kind == "START"
                    else "USER_CONFIRMED_POLICY",
                    "observed_at": NOW.isoformat(),
                    "valid_from": NOW.isoformat(),
                    "valid_to": scope.valid_until.isoformat(),
                }
            )
            sources = [
                TraceEvidence(
                    id=UUID(row["id"]),
                    user_id=USER,
                    evidence_level=cast(Any, row["evidence_level"]),
                    source_type=row["source_type"],
                    source_ref=row["source_ref"],
                    content=row["content"],
                    content_hash=row["content_hash"],
                    captured_content_hash=row["content_hash"],
                    content_integrity="VERIFIED",
                    status_at_decision="VALID",
                    observed_at=NOW,
                    valid_from=NOW,
                    valid_to=scope.valid_until if row["source_type"] == SOURCE else None,
                )
                for row in raw["evidence_items"]
            ]
            trace = build_trace(
                run_id=original.command_id,
                user_id=USER,
                phase="EVALUATION",
                as_of=NOW,
                parent_run_id=original.start_command_id,
                action_id=None,
                algorithm_versions={"full_payment_relation": original.protocol},
                inputs={
                    "kind": kind,
                    "original_request": body,
                    "request_hash": original.request_hash,
                },
                sources=sources,
                policies=[policy_copy],
                constraints=[],
                candidates=[],
                outcome={"payment_relation_command": original.model_dump(mode="json")},
            )
            commands.append(
                PeriodicOriginalCommand(
                    receipt=PaymentCommandReceipt(
                        original=original,
                        evidence_id=eid,
                        evidence_hash=raw["evidence_items"][-1]["content_hash"],
                        trace_hash=trace.trace_hash,
                        idempotent_replay=True,
                        current_scope_status="CURRENT",
                    ),
                    trace=trace,
                )
            )
    basis = {
        "user_id": str(USER),
        "as_of": NOW.isoformat(),
        "snapshot": current.snapshot.model_dump(mode="json"),
    }
    digest = configuration_hash(basis)
    facts = AutonomyFacts(
        user_id=USER,
        as_of=NOW,
        action_type="PAY_RECURRING",
        initiation="CONFIRMED_POLICY",
        authority=AuthorityAssessment(
            status="AUTHORIZED", policy_version_ids=[VERSION], evidence_ids=[EVIDENCE]
        ),
        effect=effect,
        validation=revalidate_execution(effect, current),
        payee=PayeeAssessment(status="EXISTING_CONFIRMED", evidence_ids=[EVIDENCE]),
        source_evidence_ids=[EVIDENCE],
        confirmation_reasons=["EXPLICIT_POLICY_PAYMENT_CONFIRMATION"] if not auto else [],
        source_context_hash=digest,
    )
    source = FullProtectionPolicySource(
        policy_id=FULL,
        version_id=FULL_VERSION,
        template_name="PeriodicTransferPolicy",
        configuration=full_config,
        content_hash=full_hash,
        confirmation=full_version.confirmation,
        confirmed_at=NOW,
        valid_from=NOW,
        valid_until=scope.valid_until,
        effective_status="ACTIVE",
        planning_confirmation_valid=True,
        references_current=True,
        evidence_ids=[EVIDENCE],
    )
    assert facts.validation is not None and facts.validation.projected_snapshot is not None
    projected_input = FullProtectionProjectionInput(
        snapshot=facts.validation.projected_snapshot,
        boundary_versions=current.versions,
        positions=[],
        boundary_products=[],
        policies=[source],
    )
    bounds = derive_full_account_debit_bounds(
        effect, current, facts.validation, projected_input, project_full_protection(projected_input)
    )
    veto = validate_full_execution_protection(
        effect, current, facts.validation, [source], account_debit_bounds=bounds
    )
    candidate = CandidateInput(
        candidate_key="payment:" + str(POLICY),
        facts=facts,
        execution_context=current,
        full_protection=veto,
        full_sources=[source],
        full_account_debit_bounds=bounds,
    )
    base = ActionSetInput(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        original_inventory=raw,
        inventory_reasons=[],
        expected_candidate_keys=actual_producer_keys(raw),
        candidates=[candidate],
        financial_input_hash=digest,
        financial_basis=basis,
        audit_verified=True,
        source_reasons=[],
        unsupported_producers=unsupported_producers(raw, EPOCH),
    )
    original_actual = ActualActionSetInput(
        base=base, table_coverage=coverage(raw), dynamic_goals=[], assets=[]
    )
    binding = PeriodicTransferProjectionBinding(
        status="VERIFIED_CURRENT_RELATION" if confirmed else "NO_CURRENT_DEDICATED_RELATION",
        full_policy_id=FULL,
        full_version_id=FULL_VERSION,
        relation_command_id=commands[-1].receipt.original.command_id if commands else None,
        relation_evidence_id=commands[-1].receipt.evidence_id if commands else None,
        relation_evidence_hash=commands[-1].receipt.evidence_hash if commands else None,
        scope_hash=payment_scope_hash(scope) if commands else None,
        source_evidence_ids=[EVIDENCE, commands[-1].receipt.evidence_id] if commands else [],
        source_evidence_hashes={
            str(EVIDENCE): payee["content_hash"],
            str(commands[-1].receipt.evidence_id): commands[-1].receipt.evidence_hash,
        }
        if commands
        else {},
    )
    ids = [commands[-1].receipt.evidence_id] if commands else []
    return PeriodicActionSetInput(
        original_actual_input=original_actual,
        expected_full_policy_ids=[FULL],
        relation_source_count=len(commands),
        relation_source_ids=sorted((row.receipt.evidence_id for row in commands), key=str),
        producers=[
            PeriodicPaymentProducerInput(
                candidate_key="full-periodic:" + str(FULL),
                full_policy_id=FULL,
                full_policy=full,
                relation_binding=binding,
                confirmation_evidence_ids=ids,
                commands=commands,
                candidate=candidate if confirmed else None,
            )
        ],
    )


def test_exact_300_payment_and_no_grant_replaces_only_original_scope() -> None:
    data = fixture()
    saved = data.model_dump_json()
    result = derive_periodic_payment_producers(data)
    assert result.periodic_family_complete, result.reasons + result.results[0].view.reasons
    assert result.results[0].view.state == "INCLUDED"
    assert result.results[0].view.amount_cents == 300
    assert result.results[0].view.autonomy_level == "AUTO_EXECUTE"
    assert result.results[0].shadow_original_candidate_key == "payment:" + str(POLICY)
    assert (
        not result.bank_authority
        and not result.financial_write
        and not result.full_global_adapter_installed
    )
    assert data.model_dump_json() == saved


def test_ask_does_not_inherit_prior_action_confirmation() -> None:
    data = fixture(auto=False)
    result = derive_periodic_payment_producers(data)
    assert result.periodic_family_complete, result.results[0].view.reasons
    assert result.results[0].view.autonomy_level == "ASK_ONCE"
    producer = data.producers[0]
    assert (
        producer.candidate is not None
        and producer.candidate.facts is not None
        and producer.candidate.facts.effect is not None
    )
    from app.tests.test_execution_domain import grant

    changed = producer.candidate.model_copy(
        update={
            "facts": producer.candidate.facts.model_copy(
                update={"confirmation": grant(producer.candidate.facts.effect)}
            )
        }
    )
    result = derive_periodic_payment_producers(
        data.model_copy(update={"producers": [producer.model_copy(update={"candidate": changed})]})
    )
    assert not result.periodic_family_complete and result.results[0].view.state == "UNKNOWN"


def test_actual_complete_empty_relations_excluded_but_false_empty_unknown() -> None:
    result = derive_periodic_payment_producers(fixture(confirmed=False))
    assert result.periodic_family_complete and result.results[0].view.state == "EXCLUDED", (
        result.reasons
    )
    data = fixture()
    changed = data.producers[0].model_copy(update={"confirmation_evidence_ids": [], "commands": []})
    result = derive_periodic_payment_producers(
        data.model_copy(
            update={"producers": [changed], "relation_source_count": 0, "relation_source_ids": []}
        )
    )
    assert not result.periodic_family_complete and result.results[0].view.state == "UNKNOWN"


@pytest.mark.parametrize(
    "bad",
    [
        "full_count",
        "source_count",
        "source_ids",
        "table_count",
        "owner",
        "source_hash",
        "missing_start",
        "missing_candidate",
        "wrong_account",
        "full_protection",
        "pending",
        "bank_without_receipt",
    ],
)
def test_original_denominators_and_inflight_are_never_silent_exclusions(bad: str) -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    updates: dict[str, Any] = {}
    if bad == "full_count":
        updates["expected_full_policy_ids"] = []
    elif bad == "source_count":
        updates["relation_source_count"] = 0
    elif bad == "source_ids":
        updates["relation_source_ids"] = []
    elif bad == "owner":
        raw["full_policies"][0]["user_id"] = str(UUID(int=99))
    elif bad == "source_hash":
        raw["evidence_items"][-1]["content_hash"] = "f" * 64
    elif bad in {"pending", "bank_without_receipt"}:
        aid = UUID(int=2700)
        raw["action_plans"] = [
            {
                "id": str(aid),
                "user_id": str(USER),
                "policy_id": str(POLICY),
                "status": "UNKNOWN" if bad == "pending" else "SUBMITTED",
            }
        ]
        if bad == "bank_without_receipt":
            raw["bank_operations"] = [
                {
                    "id": str(UUID(int=2701)),
                    "user_id": str(USER),
                    "action_plan_id": str(aid),
                    "status": "SETTLED",
                }
            ]
    else:
        producer = data.producers[0]
        candidate = producer.candidate
        assert (
            candidate is not None
            and candidate.facts is not None
            and candidate.facts.effect is not None
        )
        if bad == "missing_start":
            producer = producer.model_copy(update={"commands": producer.commands[1:]})
        elif bad == "missing_candidate":
            producer = producer.model_copy(update={"candidate": None})
        elif bad == "full_protection":
            producer = producer.model_copy(
                update={"candidate": candidate.model_copy(update={"full_protection": None})}
            )
        elif bad == "wrong_account":
            raw["accounts"][0]["external_ref"] = "tampered-identity"
        updates["producers"] = [producer]
    base = data.original_actual_input.base.model_copy(update={"original_inventory": raw})
    proof = coverage(raw)
    if bad == "table_count":
        proof[0] = proof[0].model_copy(update={"actual_count": 999})
    updates["original_actual_input"] = data.original_actual_input.model_copy(
        update={"base": base, "table_coverage": proof}
    )
    result = derive_periodic_payment_producers(data.model_copy(update=updates))
    assert not result.periodic_family_complete, (bad, result)
    assert result.handled_unsupported_codes == []


def test_other_family_unknown_and_original_snapshot_are_retained() -> None:
    data = fixture(confirmed=False)
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["full_policies"].append(
        {
            "id": str(UUID(int=2790)),
            "user_id": str(USER),
            "epoch_id": str(EPOCH),
            "template_name": "RecoveryPolicy",
            "status": "ACTIVE",
        }
    )
    base = data.original_actual_input.base.model_copy(
        update={
            "original_inventory": raw,
            "unsupported_producers": unsupported_producers(raw, EPOCH),
        }
    )
    data = data.model_copy(
        update={
            "original_actual_input": data.original_actual_input.model_copy(
                update={"base": base, "table_coverage": coverage(raw)}
            )
        }
    )
    result = derive_periodic_payment_producers(data)
    assert result.periodic_family_complete
    assert any("RecoveryPolicy" in x for x in result.remaining_unsupported_producers)
    assert "ACTUAL_UNSUPPORTED_CURRENT_PRODUCERS" in result.original_actual_reasons
    assert result.full_global_adapter_installed is False


def test_frozen_exact_algorithm_recomputes_original_result_and_complete_sources() -> None:
    data = fixture()
    result = derive_periodic_payment_producers(data)
    commands = data.producers[0].commands
    sources = {row.id: row for cmd in commands for row in cmd.trace.sources}
    original = build_trace(
        run_id=UUID(int=2780),
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        action_id=None,
        algorithm_versions={"periodic_action_producers": ALGORITHM},
        inputs={"periodic_action_set_input": data.model_dump(mode="json")},
        sources=list(sources.values()),
        policies=commands[0].trace.policies,
        constraints=[],
        candidates=[],
        outcome={"periodic_action_set_result": result.model_dump(mode="json")},
    )
    assert service.verify_frozen_periodic_payment_producers(original) == result
    content = original.model_dump(exclude={"trace_hash", "input_hash"})
    content["algorithm_versions"] = {
        "periodic_action_producers": "full-policy-action-set-boundary-actual-v2"
    }
    with pytest.raises(ValueError):
        service.verify_frozen_periodic_payment_producers(build_trace(**content))
    content = original.model_dump(exclude={"trace_hash", "input_hash"})
    content["sources"] = []
    with pytest.raises(ValueError):
        service.verify_frozen_periodic_payment_producers(build_trace(**content))
    content = original.model_dump(exclude={"trace_hash", "input_hash"})
    content["outcome"]["periodic_action_set_result"]["results"][0]["view"]["amount_cents"] = 301
    with pytest.raises(ValueError):
        service.verify_frozen_periodic_payment_producers(build_trace(**content))


def test_actual_http_get_is_read_only_and_query_facts_are_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = derive_periodic_payment_producers(fixture(confirmed=False))
    seen: list[UUID] = []

    def read(_s: Session, user: UUID, _now: Any) -> Any:
        seen.append(user)
        return result

    monkeypatch.setattr(api, "read_current_periodic_payment_producers", read)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: cast(Session, object())
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    with TestClient(app) as client:
        assert client.get(
            "/api/v1/boundary/periodic-action-producers/current"
        ).json() == result.model_dump(mode="json")
        assert (
            client.get(
                "/api/v1/boundary/periodic-action-producers/current?amount_cents=1"
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/boundary/periodic-action-producers/current",
                json={"bank_status": "SETTLED"},
            ).status_code
            == 405
        )
    assert seen == [USER]


def test_suspended_relation_still_preserves_original_unknown_bank_responsibility() -> None:
    data = fixture()
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["full_policies"][0]["status"] = "SUSPENDED"
    raw["action_plans"] = [
        {
            "id": str(UUID(int=2900)),
            "user_id": str(USER),
            "policy_id": str(POLICY),
            "status": "UNKNOWN",
        }
    ]
    assert data.producers[0].full_policy is not None
    producer = data.producers[0].model_copy(
        update={
            "full_policy": data.producers[0].full_policy.model_copy(
                update={"status": "SUSPENDED", "effective_status": "SUSPENDED"}
            )
        }
    )
    base = data.original_actual_input.base.model_copy(
        update={
            "original_inventory": raw,
            "unsupported_producers": unsupported_producers(raw, EPOCH),
        }
    )
    result = derive_periodic_payment_producers(
        data.model_copy(
            update={
                "producers": [producer],
                "original_actual_input": data.original_actual_input.model_copy(
                    update={"base": base, "table_coverage": coverage(raw)}
                ),
            }
        )
    )
    assert not result.periodic_family_complete and result.results[0].view.state == "UNKNOWN"
    assert result.results[0].unresolved_original_action_ids == [UUID(int=2900)]


def test_receipt_current_string_cannot_hide_current_original_confirmation() -> None:
    data = fixture()
    producer = data.producers[0]
    commands = [
        producer.commands[0],
        producer.commands[1].model_copy(
            update={
                "receipt": producer.commands[1].receipt.model_copy(
                    update={"current_scope_status": "STALE"}
                )
            }
        ),
    ]
    producer = producer.model_copy(
        update={
            "commands": commands,
            "relation_binding": PeriodicTransferProjectionBinding(
                status="NO_CURRENT_DEDICATED_RELATION",
                full_policy_id=FULL,
                full_version_id=FULL_VERSION,
            ),
        }
    )
    result = derive_periodic_payment_producers(data.model_copy(update={"producers": [producer]}))
    assert not result.periodic_family_complete and result.results[0].view.state == "UNKNOWN"
    assert result.results[0].view.reasons == ["PERIODIC_CURRENT_STATUS_NOT_REPRODUCIBLE"]


def test_source_bound_service_captures_complete_originals_and_restores_invocation_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = fixture()
    producer = data.producers[0]
    retained = DecisionCapture()
    fake = SimpleNamespace(
        info={CAPTURE_KEY: retained}, get=lambda _model, _id: SimpleNamespace(is_simulated=True)
    )
    monkeypatch.setattr(service, "_snapshot", lambda _session: None)
    monkeypatch.setattr(
        service,
        "current_audit_epoch",
        lambda _session, _user: SimpleNamespace(id=EPOCH, status="OPEN"),
    )
    monkeypatch.setattr(service, "read_full_policy", lambda _s, _u, _id, _now: producer.full_policy)
    commands = {row.receipt.original.command_id: row for row in producer.commands}
    monkeypatch.setattr(service, "_original", lambda _s, _u, id, _now, **_kw: commands[id].receipt)
    monkeypatch.setattr(
        service,
        "get_decision_trace",
        lambda _s, _u, id, _now: SimpleNamespace(
            trace=commands[id].trace, completeness="COMPLETE", audit_chain_status="VALID"
        ),
    )
    monkeypatch.setattr(
        service,
        "verified_periodic_transfer_projection_binding",
        lambda _s, _u, _full, _now: producer.relation_binding,
    )
    monkeypatch.setattr(
        service, "_candidate", lambda _s, _u, _now, _original, _receipt: producer.candidate
    )
    monkeypatch.setattr(service, "capture_evidence", lambda _s, _u, _ids: None)
    original = ActualActionSetCapture(
        data.original_actual_input,
        derive_actual_action_set(data.original_actual_input),
        DecisionCapture(),
    )
    captured = service.capture_current_periodic_payment_producers(
        cast(Session, fake), USER, NOW, original_actual_capture=original
    )
    assert (
        captured.result.periodic_family_complete
        and captured.result.results[0].view.amount_cents == 300
    )
    assert fake.info[CAPTURE_KEY] is retained and len(retained.sources) == 3
    assert captured.inputs.relation_source_count == 2
    monkeypatch.setattr(
        service,
        "get_decision_trace",
        lambda _s, _u, id, _now: SimpleNamespace(
            trace=commands[id].trace, completeness="INCOMPLETE", audit_chain_status="VALID"
        ),
    )
    missing = service.capture_current_periodic_payment_producers(
        cast(Session, fake), USER, NOW, original_actual_capture=original
    )
    assert not missing.result.periodic_family_complete
    assert missing.result.results[0].view.state == "UNKNOWN"
    assert missing.inputs.relation_source_count == 2 and fake.info[CAPTURE_KEY] is retained
    # Malformed original scope must be UNKNOWN, not a filtered empty relation or a crash.
    raw = copy.deepcopy(data.original_actual_input.base.original_inventory)
    raw["evidence_items"][-1]["content"]["scope"] = 0
    raw["evidence_items"][-1]["content_hash"] = configuration_hash(
        raw["evidence_items"][-1]["content"]
    )
    bad_base = data.original_actual_input.base.model_copy(update={"original_inventory": raw})
    bad_input = data.original_actual_input.model_copy(
        update={"base": bad_base, "table_coverage": coverage(raw)}
    )
    original = ActualActionSetCapture(
        bad_input, derive_actual_action_set(bad_input), DecisionCapture()
    )
    malformed = service.capture_current_periodic_payment_producers(
        cast(Session, fake), USER, NOW, original_actual_capture=original
    )
    assert (
        not malformed.result.periodic_family_complete
        and malformed.inputs.relation_source_count == 2
    )
    assert any(
        "PERIODIC_COMPLETE_RELATION_ROW_INVALID" in reason
        for reason in malformed.inputs.source_reasons
    )
