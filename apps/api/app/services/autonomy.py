"""Read-only autonomy assessment from trusted application and bank facts."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    AssetPosition,
    AssetProduct,
    BankOperation,
    Goal,
    Policy,
    PolicyVersion,
)
from app.domain.autonomy import classify_autonomy
from app.domain.autonomy_types import (
    AuthorityAssessment,
    AutonomyDecision,
    AutonomyFacts,
    AutonomyWorld,
    FiniteUserVariable,
    PayeeAssessment,
)
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundaryModel, BoundaryResult, SourceIssue
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.action_contracts import ActionIntent, TransferIntent
from app.services.asset_exposure_import import load_asset_exposure
from app.services.boundary import BoundaryContext, load_boundary_context
from app.services.execution_context import load_execution_context
from app.services.execution_planning import plan_execution_effect
from app.services.execution_sources import (
    load_execution_quote,
    read_execution_confirmation,
    resolve_payee_binding,
)
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, effective_status
from app.services.simulated_bank import validate_bank_projection
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


class AutonomyResponse(BoundaryModel):
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    status: Literal["ASSESSED"] = "ASSESSED"
    action_id: UUID | None = None
    decision: AutonomyDecision
    effect: ExecutionEffect | None = None
    source_evidence_ids: list[UUID] = Field(default_factory=list)
    input_digest: str


@dataclass
class _Basis:
    context: BoundaryContext
    boundary: BoundaryResult
    issues: list[SourceIssue]
    digest: str


def _clock(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时钟必须带时区")
    return now.astimezone(UTC)


def _basis(session: Session, user_id: UUID, now: datetime) -> _Basis:
    base = load_boundary_context(session, user_id, now)
    exposure = load_asset_exposure(session, base, {"scope": "general_idle_funds"})
    try:
        validate_bank_projection(session, user_id, now)
    except PolicyLifecycleError as error:
        base.sources.issue(error.code, "independent_bank", error.message)
    income = None
    if any(
        row.source_type == "SIMULATED_NEW_FUNDS_LEDGER" and row.status != "SUPERSEDED"
        for row in base.sources.evidence.values()
    ):
        try:
            state = read_income_state(session, user_id, now)
            income = state.ledger.model_dump(mode="json")
        except PolicyLifecycleError as error:
            base.sources.issue(error.code, "income_ledger", error.message)
    issues = [
        SourceIssue(code=i.code, entity_type="source", entity_id=i.source_ref)
        for i in base.sources.issues
    ]
    base = replace(base, snapshot=base.snapshot.model_copy(update={"source_issues": issues}))
    boundary = compute_boundary(base.snapshot, base.versions, base.positions, base.products)
    # Independent bank facts are bound by the verified complete exposure manifest,
    # not inferred from editable application balances. No proposed effect enters this digest.
    products = [
        {column.name: getattr(row, column.name) for column in AssetProduct.__table__.columns}
        for row in session.scalars(select(AssetProduct).order_by(AssetProduct.id))
    ]
    payload = {
        "user_id": str(user_id),
        "as_of": now.isoformat(),
        "snapshot": base.snapshot.model_dump(mode="json"),
        "versions": [v.model_dump(mode="json") for v in base.versions],
        "positions": [p.model_dump(mode="json") for p in base.positions],
        "products": products,
        "exposure": exposure.model_dump(mode="json"),
        "income": income,
        "evidence": [
            {"id": str(key), "hash": base.sources.evidence[key].content_hash}
            for key in sorted(base.sources.used)
        ],
    }
    digest = configuration_hash(json.loads(json.dumps(payload, sort_keys=True, default=str)))
    return _Basis(base, boundary, issues, digest)


def _owned[Owned: (Account, AssetPosition, Goal, Policy)](
    session: Session,
    model: type[Owned],
    user_id: UUID,
    identity: UUID,
) -> Owned:
    row = session.get(model, identity)
    if row is None or row.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "指定对象不存在", 404)
    return row


def _intent_policies(session: Session, user_id: UUID, intent: ActionIntent) -> list[UUID]:
    if intent.kind == "transfer_internal":
        _owned(session, Account, user_id, intent.source_account_id)
        _owned(session, Account, user_id, intent.destination_account_id)
        return []
    if intent.kind == "pay_recurring" or intent.kind == "purchase_asset":
        _owned(session, Policy, user_id, intent.policy_id)
        return [intent.policy_id]
    if intent.kind == "allocate_goal":
        goal = _owned(session, Goal, user_id, intent.goal_id)
        assert isinstance(goal, Goal)
        return [goal.policy_id]
    position = _owned(session, AssetPosition, user_id, intent.position_id)
    assert isinstance(position, AssetPosition)
    if position.policy_version_id is None:
        return []
    version = session.get(PolicyVersion, position.policy_version_id)
    if version is None or version.user_id != user_id:
        raise PolicyLifecycleError("INVALID_EXECUTION_SOURCE", "原持仓授权缺失")
    return [version.policy_id]


def _authority(
    session: Session,
    user_id: UUID,
    policy_ids: list[UUID],
    now: datetime,
    *,
    expected: list[UUID] | None = None,
) -> AuthorityAssessment:
    versions: list[UUID] = []
    proofs: set[UUID] = set()
    outside: list[str] = []
    for identity in sorted(set(policy_ids)):
        policy = _owned(session, Policy, user_id, identity)
        assert isinstance(policy, Policy)
        version = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == identity, PolicyVersion.user_id == user_id)
            .order_by(PolicyVersion.version_number.desc())
        )
        if version is None:
            return AuthorityAssessment(
                status="MISSING_EVIDENCE", reasons=["MISSING_FORMAL_POLICY_VERSION"]
            )
        versions.append(version.id)
        if expected is not None and version.id not in expected:
            return AuthorityAssessment(
                status="STALE_VERSION",
                policy_version_ids=versions,
                reasons=["STALE_ACTION_POLICY_VERSION"],
            )
        try:
            configuration = validate_configuration(version.configuration)
            evidence = _evidence(session, user_id, version.evidence_ids, now, lock=False)
            if (
                version.confirmed_at is None
                or version.confirmed_at > now
                or version.valid_from is None
            ):
                raise ValueError("Missing confirmed policy window")
            binding = {
                "user_id": str(user_id),
                "policy_id": str(identity),
                "version_id": str(version.id),
                "reviewed_hash": version.content_hash,
                "confirmed_at": version.confirmed_at.isoformat(),
                "accepted": True,
            }
            if (
                configuration_hash(configuration) != version.content_hash
                or any(version.confirmation.get(k) != v for k, v in binding.items())
                or not any(
                    item.evidence_level == "USER_CONFIRMED_POLICY"
                    and item.source_type == "POLICY_CONFIRMATION"
                    and item.source_ref == str(version.id)
                    and item.content == version.confirmation
                    for item in evidence
                )
            ):
                raise ValueError("Formal confirmation is not bound to this version")
            proofs.update(item.id for item in evidence)
        except (PolicyLifecycleError, ValueError, TypeError):
            return AuthorityAssessment(
                status="MISSING_EVIDENCE",
                policy_version_ids=versions,
                reasons=["INVALID_FORMAL_POLICY_EVIDENCE"],
            )
        status = effective_status(policy, version, now)
        if status != "ACTIVE":
            outside.append("POLICY_" + status)
    return AuthorityAssessment(
        status=("STALE_VERSION" if expected is not None else "OUTSIDE_AUTHORITY")
        if outside
        else "AUTHORIZED",
        policy_version_ids=versions,
        evidence_ids=sorted(proofs),
        reasons=outside,
    )


def _facts(
    session: Session,
    user_id: UUID,
    intent: ActionIntent,
    now: datetime,
    basis: _Basis,
    *,
    effect: ExecutionEffect | None = None,
    action_id: UUID | None = None,
) -> AutonomyFacts:
    policy_ids = _intent_policies(session, user_id, intent)
    authority = _authority(
        session, user_id, policy_ids, now, expected=effect.policy_version_ids if effect else None
    )
    if intent.kind == "transfer_internal":
        # This proves the two accounts belong to the explicitly requesting user.
        # It is not a persistent policy grant: the pure classifier still requires
        # exact one-shot confirmation of every transfer.
        account_ids = {intent.source_account_id, intent.destination_account_id}
        relation_proofs = sorted(
            {
                proof
                for row in basis.context.snapshot.cash_accounts
                if row.account_id in account_ids
                for proof in row.evidence_ids
            }
        )
        authority = authority.model_copy(update={"evidence_ids": relation_proofs})
    source_ids = sorted(basis.context.sources.used | set(authority.evidence_ids))
    facts = AutonomyFacts(
        user_id=user_id,
        as_of=now,
        action_type=intent.kind.upper(),
        initiation="USER_EXPLICIT" if intent.kind == "transfer_internal" else "CONFIRMED_POLICY",
        authority=authority,
        source_evidence_ids=source_ids,
        source_issues=basis.issues,
        source_context_hash=basis.digest,
    )
    if basis.issues or authority.status in {"MISSING_EVIDENCE", "STALE_VERSION"}:
        return facts
    if intent.kind == "redeem_asset" and not policy_ids:
        return facts.model_copy(
            update={
                "authority": AuthorityAssessment(
                    status="OUTSIDE_AUTHORITY",
                    evidence_ids=source_ids,
                    reasons=["NO_ORIGINAL_AUTOMATIC_AUTHORITY"],
                ),
                "hard_block_reasons": ["BASELINE_LIQUIDITY_RISK"]
                if basis.boundary.status == "LIQUIDITY_RISK"
                else [],
            }
        )
    payee = PayeeAssessment()
    if intent.kind == "pay_recurring":
        version = session.get(PolicyVersion, authority.policy_version_ids[0])
        assert version is not None
        try:
            payee_id = version.configuration["payee_id"]
            proof = resolve_payee_binding(session, user_id, payee_id, now, bill_id=intent.bill_id)
            payee = PayeeAssessment(status="EXISTING_CONFIRMED", evidence_ids=[proof])
            source_ids = sorted(set(source_ids) | {proof})
        except (PolicyLifecycleError, KeyError):
            return facts.model_copy(
                update={
                    "payee": PayeeAssessment(status="UNSUPPORTED"),
                    "hard_block_reasons": ["UNSUPPORTED_PAYEE_RELATIONSHIP"],
                }
            )
    if authority.status == "OUTSIDE_AUTHORITY":
        return facts.model_copy(
            update={
                "payee": payee,
                "source_evidence_ids": source_ids,
                "hard_block_reasons": ["BASELINE_LIQUIDITY_RISK"]
                if basis.boundary.status == "LIQUIDITY_RISK"
                else [],
            }
        )
    try:
        if effect is None:
            operation_id = uuid5(
                user_id,
                "autonomy:"
                + configuration_hash(
                    {"intent": intent.model_dump(mode="json"), "as_of": now.isoformat()}
                ),
            )
            effect = plan_execution_effect(session, user_id, operation_id, intent, now)
        if effect.policy_version_ids:
            bound = [session.get(PolicyVersion, key) for key in effect.policy_version_ids]
            if any(v is None or v.user_id != user_id for v in bound):
                raise PolicyLifecycleError("INVALID_EXECUTION_AUTHORITY", "动作精确权限版本缺失")
            authority = _authority(
                session,
                user_id,
                [v.policy_id for v in bound if v is not None],
                now,
                expected=effect.policy_version_ids,
            )
            source_ids = sorted(set(source_ids) | set(authority.evidence_ids))
            facts = facts.model_copy(update={"authority": authority})
        context = load_execution_context(session, user_id, effect, now, own_action_id=action_id)
        confirmation = read_execution_confirmation(session, effect, now) if action_id else None
        validation = revalidate_execution(effect, context, confirmation=confirmation)
    except PolicyLifecycleError as error:
        if error.status_code == 404:
            raise
        return facts.model_copy(update={"payee": payee, "hard_block_reasons": [error.code]})
    # Only a typed, known permission denial may have a financial-only counterfactual.
    # This copy is confined to assessment: it is never passed back to the executor,
    # and OUTSIDE_AUTHORITY forces a non-executable recommendation after checking risk.
    denied = [
        issue
        for issue in context.source_issues
        if issue.code == "EXECUTION_REDEMPTION_PERMISSION_DENIED"
    ]
    other_issues = [
        issue
        for issue in context.source_issues
        if issue.code != "EXECUTION_REDEMPTION_PERMISSION_DENIED"
    ]
    if denied and not other_issues:
        if not (effect.fee_cents or effect.loss_cents):
            return facts.model_copy(
                update={
                    "authority": authority.model_copy(
                        update={
                            "status": "OUTSIDE_AUTHORITY",
                            "reasons": ["EXECUTION_REDEMPTION_PERMISSION_DENIED"],
                        }
                    ),
                    "hard_block_reasons": ["BASELINE_LIQUIDITY_RISK"]
                    if basis.boundary.status == "LIQUIDITY_RISK"
                    else [],
                }
            )
        assert effect.position_id is not None
        try:
            quote = load_execution_quote(
                session, user_id, effect.position_id, now, requested_at=effect.valid_from
            )
            financial_context = context.model_copy(
                update={"source_issues": [], "redemption_quote": quote}
            )
            validation = revalidate_execution(effect, financial_context, confirmation=confirmation)
        except PolicyLifecycleError as error:
            return facts.model_copy(update={"hard_block_reasons": [error.code]})
        facts = facts.model_copy(
            update={
                "authority": authority.model_copy(
                    update={
                        "status": "OUTSIDE_AUTHORITY",
                        "reasons": ["EXECUTION_REDEMPTION_PERMISSION_DENIED"],
                    }
                )
            }
        )
        context = financial_context
    if context.redemption_quote is not None:
        source_ids = sorted(set(source_ids) | set(context.redemption_quote.evidence_ids))
    if confirmation is not None:
        source_ids = sorted(set(source_ids) | {confirmation.evidence_id})
    reasons = ["EXPLICIT_POLICY_PAYMENT_CONFIRMATION"] if context.requires_confirmation else []
    return facts.model_copy(
        update={
            "effect": effect,
            "action_type": effect.action_type,
            "validation": validation,
            "confirmation": confirmation,
            "confirmation_reasons": reasons,
            "source_issues": [*basis.issues, *context.source_issues],
            "source_evidence_ids": source_ids,
            "payee": payee,
        }
    )


def _response(
    facts: AutonomyFacts,
    *,
    action_id: UUID | None = None,
    uncertainty: FiniteUserVariable | None = None,
) -> AutonomyResponse:
    decision = classify_autonomy(facts, uncertainty)
    return AutonomyResponse(
        user_id=facts.user_id,
        as_of=facts.as_of,
        action_id=action_id,
        decision=decision,
        effect=None if uncertainty is not None else facts.effect,
        source_evidence_ids=facts.source_evidence_ids,
        input_digest=configuration_hash(
            {
                "facts": facts.model_dump(mode="json"),
                "uncertainty": uncertainty.model_dump(mode="json") if uncertainty else None,
            }
        ),
    )


def assess_intent(
    session: Session, user_id: UUID, intent: ActionIntent, now: datetime
) -> AutonomyResponse:
    now = _clock(now)
    with session.no_autoflush:
        _intent_policies(session, user_id, intent)
        basis = _basis(session, user_id, now)
        return _response(_facts(session, user_id, intent, now, basis))


def assess_action(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> AutonomyResponse:
    now = _clock(now)
    with session.no_autoflush:
        row = session.get(ActionPlan, action_id)
        if row is None or row.user_id != user_id or "execution" not in row.request:
            raise PolicyLifecycleError("NOT_FOUND", "执行动作不存在", 404)
        operation = session.scalar(
            select(BankOperation).where(BankOperation.action_plan_id == action_id)
        )
        if operation is not None or row.status in {
            "SUBMITTED",
            "UNKNOWN",
            "SUCCEEDED",
            "RECONCILED",
        }:
            raise PolicyLifecycleError(
                "NO_RECLASSIFICATION_AFTER_ACCEPTANCE",
                "已提交动作请读取原动作和银行回执，不重新分级或授权",
                409,
            )
        from app.services.action_contracts import PrepareActionRequest

        try:
            command = BankCommand.model_validate_json(json.dumps(row.request["execution"]))
            if (
                configuration_hash(row.request) != row.request_hash
                or command.effect_hash != execution_effect_hash(command.effect)
                or command.effect.operation_id != action_id
                or command.effect.user_id != user_id
            ):
                raise ValueError("Original immutable action was altered")
            intent = PrepareActionRequest.model_validate_json(
                json.dumps({"idempotency_key": "assessment", "intent": row.request["intent"]})
            ).intent
        except (TypeError, ValueError, KeyError) as error:
            raise PolicyLifecycleError(
                "INVALID_EXECUTION_SOURCE", "原动作载荷不完整或已变化", 409
            ) from error
        basis = _basis(session, user_id, now)
        facts = _facts(
            session, user_id, intent, now, basis, effect=command.effect, action_id=action_id
        )
        if row.status not in {"PLANNED", "AUTHORIZED"}:
            facts = facts.model_copy(
                update={"hard_block_reasons": ["OLD_ACTION_REQUIRES_NEW_PREPARATION"]}
            )
        return _response(facts, action_id=action_id)


def assess_transfer_preferences(
    session: Session,
    user_id: UUID,
    source_account_id: UUID,
    destination_account_id: UUID,
    amount_options: list[int],
    now: datetime,
) -> AutonomyResponse:
    if (
        not 2 <= len(amount_options) <= 8
        or len(set(amount_options)) != len(amount_options)
        or any(
            type(amount) is not int or not 0 < amount <= 9223372036854775807
            for amount in amount_options
        )
    ):
        raise PolicyLifecycleError("INVALID_USER_OPTIONS", "仅接受2至8个不重复的正整数分金额")
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError("UNCOMMITTED_ASSESSMENT_INPUT", "有限候选仅评估已提交事实")
    now = _clock(now)
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    # Separate connection: caller may already have a READ COMMITTED transaction.
    # Isolation is selected before BEGIN/any query; no caller transaction is changed.
    # Flushed but uncommitted caller changes are intentionally not visible here.
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        with Session(bind=connection, autoflush=False) as reading:
            return _transfer_worlds(
                reading, user_id, source_account_id, destination_account_id, amount_options, now
            )


def _transfer_worlds(
    session: Session,
    user_id: UUID,
    source_account_id: UUID,
    destination_account_id: UUID,
    amount_options: list[int],
    now: datetime,
) -> AutonomyResponse:
    with session.no_autoflush:
        intents = [
            TransferIntent(
                kind="transfer_internal",
                source_account_id=source_account_id,
                destination_account_id=destination_account_id,
                amount_cents=amount,
            )
            for amount in sorted(amount_options)
        ]
        _intent_policies(session, user_id, intents[0])
        basis = _basis(session, user_id, now)
        worlds = [
            AutonomyWorld(
                candidate_key=str(intent.amount_cents),
                facts=_facts(session, user_id, intent, now, basis),
            )
            for intent in intents
        ]
        variable = FiniteUserVariable(
            variable_id="transfer_amount",
            kind="USER_PREFERENCE",
            completeness="COMPLETE",
            evidence_ids=sorted(basis.context.sources.used),
            source_context_hash=basis.digest,
            worlds=worlds,
        )
        return _response(worlds[0].facts, uncertainty=variable)
