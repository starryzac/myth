"""Signed USER review -> original lifecycle commit -> independent actual readback."""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.db.base import Base
from app.db.models import User
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_policy_change_multi import SourceKind
from app.domain.full_policy_reviewed_change import (
    ALGORITHM,
    EXCLUDED_METADATA,
    GLOBAL_TABLES,
    MARKER,
    ConfirmReviewedChangeRequest,
    ReviewedChangeRecord,
    ReviewRequest,
    SourceBasis,
    coverage,
    impact_value,
    legacy_request,
    outer_request,
    read_review_original,
    require_current_review,
    review_digest,
    review_original_text,
    verify_frozen_reviewed_policy_change_trace,
    verify_record,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope, current_audit_epoch, row_copy
from app.services.decision_trace import capture_sources, get_decision_trace, record_trace
from app.services.full_policy_change_multi import (
    MultiTemplatePreviewRequest,
    MultiTemplatePreviewResponse,
    preview_multi_template_financial_change,
)
from app.services.full_policy_lifecycle import FullChangeRequest, change_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user, change_policy
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NAMESPACE = UUID("dd333318-abf3-497f-8945-455e0f191107")


class ReviewCommitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol: Literal["reviewed-policy-change-commit-v1"] = "reviewed-policy-change-commit-v1"
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    full_financial_effects_verified: Literal[False] = False
    user_id: UUID
    source_kind: SourceKind
    policy_id: UUID
    idempotency_key: str
    original_request: ConfirmReviewedChangeRequest
    outer_request_hash: str
    legacy_request_hash: str
    commit_state: Literal["COMMITTED"]
    status: Literal[
        "COMMITTED_REVIEW_SCOPE_MATCHED",
        "COMMITTED_BUT_FINANCIAL_UNKNOWN",
    ]
    lifecycle_receipt: dict[str, Any] | None
    actual_version_id: UUID | None = None
    actual_configuration_hash: str | None = None
    reviewed_preview: MultiTemplatePreviewResponse
    actual_readback: MultiTemplatePreviewResponse | None = None
    covered_scopes: list[str]
    uncovered_items: list[str]
    changed_source_tables: list[str]
    actual_delta_safe_idle_cents: int | None = None
    actual_delta_minimum_margin_cents: int | None = None
    reasons: list[str]


class ReviewCommandLookup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    current_authority: Literal[False] = False
    not_found_is_final: Literal[False] = False
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    user_id: UUID
    idempotency_key: str
    original_request: ConfirmReviewedChangeRequest | None = None
    response: ReviewCommitResponse | None = None


def _fail(
    code: str, message: str = "当前实际原件、完整复核范围或原键绑定不一致"
) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def identity(user_id: UUID, kind: str, key: str) -> UUID:
    if not key or len(key) > 160 or re.fullmatch(r"[A-Za-z0-9_.:-]+", key) is None:
        raise _fail("INVALID_REVIEW_KEY")
    return uuid5(NAMESPACE, f"{user_id}:{kind}:{key}")


@contextmanager
def _reader(engine: Engine) -> Iterator[Session]:
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        with Session(bind=connection) as session, audit_read_scope(session):
            yield session


def _principal(principal: LocalActorPrincipal, user_id: UUID, now: datetime) -> None:
    try:
        require_local_user(principal, user_id, now)
    except ValueError as cause:
        raise _fail("CURRENT_SIGNED_USER_REVIEW_REQUIRED") from cause


def _basis(session: Session, user_id: UUID, epoch_id: UUID, now: datetime) -> SourceBasis:
    """All currently registered source models, all original columns, no row clipping."""
    user = session.get(User, user_id)
    epoch = current_audit_epoch(session, user_id)
    if (
        user is None
        or not user.is_simulated
        or epoch is None
        or epoch.id != epoch_id
        or epoch.status != "OPEN"
    ):
        raise _fail("CURRENT_OWNER_OPEN_EPOCH_REQUIRED")
    models = sorted(
        (mapper.class_ for mapper in Base.registry.mappers), key=lambda model: model.__table__.name
    )
    names = [model.__table__.name for model in models]
    if len(names) != len(set(names)):
        raise _fail("REGISTERED_SOURCE_MODEL_AMBIGUOUS")
    tables: dict[str, list[dict[str, Any]]] = {}
    excluded = []
    for model in models:
        name = model.__table__.name
        if name in EXCLUDED_METADATA:
            excluded.append(name)
            continue
        if model is User:
            query = select(model).where(model.id == user_id)
        elif "user_id" in model.__table__.columns:
            query = select(model).where(model.user_id == user_id)
        elif name in GLOBAL_TABLES:
            query = select(model)
        else:
            raise _fail("UNREGISTERED_GLOBAL_SOURCE_MODEL", name)
        rows: list[Any] = list(session.scalars(query.order_by(model.id).limit(10001)))
        if len(rows) > 10000:
            raise _fail("COMPLETE_REVIEW_SOURCE_CAPACITY_EXCEEDED", name)
        tables[name] = [row_copy(row) for row in rows]
    return SourceBasis(
        user_id=user_id,
        epoch_id=epoch_id,
        local_day=now.astimezone(ZoneInfo(user.timezone)).date().isoformat(),
        registered_tables=names,
        excluded_metadata_tables=excluded,
        tables=tables,
        row_counts={name: len(rows) for name, rows in tables.items()},
    )


def _capture(
    engine: Engine,
    user_id: UUID,
    kind: SourceKind,
    policy_id: UUID,
    body: ReviewRequest,
    now: datetime,
) -> tuple[MultiTemplatePreviewResponse, SourceBasis]:
    with _reader(engine) as read:
        preview = preview_multi_template_financial_change(
            read,
            user_id,
            kind,
            policy_id,
            MultiTemplatePreviewRequest(
                expected_version_id=body.expected_version_id,
                expected_epoch_id=body.expected_epoch_id,
                configuration=body.configuration,
            ),
            now,
        )
        return preview, _basis(read, user_id, body.expected_epoch_id, now)


def _original(session: Session, user_id: UUID, run_id: UUID, now: datetime) -> DecisionTrace:
    result = get_decision_trace(session, user_id, run_id, now)
    if (
        result.trace is None
        or result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
    ):
        raise _fail("ORIGINAL_REVIEW_TRACE_AND_AUDIT_REQUIRED")
    try:
        verify_frozen_reviewed_policy_change_trace(result.trace)
    except (TypeError, ValueError, KeyError) as cause:
        raise _fail("ORIGINAL_REVIEW_TRACE_INVALID") from cause
    return result.trace


def read_review(
    engine: Engine, user_id: UUID, review_id: UUID, now: datetime
) -> ReviewedChangeRecord:
    with _reader(engine) as read:
        trace = _original(read, user_id, review_id, _now(now))
        if trace.inputs.get("review_phase") != "REVIEW":
            raise _fail("ORIGINAL_REVIEW_ID_REQUIRED")
        return read_review_original(trace.inputs)


def register_review(
    engine: Engine,
    user_id: UUID,
    kind: SourceKind,
    policy_id: UUID,
    body: ReviewRequest,
    now: datetime,
    principal: LocalActorPrincipal,
) -> ReviewedChangeRecord:
    now = _now(now)
    _principal(principal, user_id, now)
    run_id = identity(user_id, "REVIEW", body.idempotency_key)
    with Session(engine) as write, write.begin():
        _user(write, user_id)
        from app.db.models import DecisionRun

        if write.get(DecisionRun, run_id) is not None:
            trace = _original(write, user_id, run_id, now)
            record = read_review_original(trace.inputs)
            if (record.source_kind, record.policy_id, record.request) != (kind, policy_id, body):
                raise _fail("IDEMPOTENCY_CONFLICT")
            return record
        preview, basis = _capture(engine, user_id, kind, policy_id, body, now)
        covered, uncovered, eligible = coverage(preview)
        record = ReviewedChangeRecord(
            review_id=run_id,
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            source_kind=kind,
            policy_id=policy_id,
            request=body,
            captured_at=now,
            expires_at=min(now + timedelta(minutes=15), principal.expires_at),
            actor=principal,
            source_basis=basis,
            source_basis_hash=configuration_hash(basis.model_dump(mode="json")),
            preview=preview,
            covered_scopes=covered,
            uncovered_items=uncovered,
            confirmation_eligible=eligible,
            review_hash="0" * 64,
        )
        record = record.model_copy(update={"review_hash": review_digest(record)})
        verify_record(record)
        record_trace(
            write,
            build_trace(
                run_id=run_id,
                user_id=user_id,
                phase="EVALUATION",
                as_of=now,
                algorithm_versions={MARKER: ALGORITHM},
                inputs={
                    "review_phase": "REVIEW",
                    "review_record_json": review_original_text(record),
                    "review_record_hash": configuration_hash(record.model_dump(mode="json")),
                },
                sources=capture_sources(write, user_id, preview.source_evidence_ids),
                policies=[],
                constraints=[],
                candidates=[],
                outcome={
                    "review_hash": record.review_hash,
                    "confirmation_eligible": eligible,
                    "bank_authority": False,
                },
            ),
        )
        return record


def _outer(
    user_id: UUID, kind: SourceKind, policy_id: UUID, body: ConfirmReviewedChangeRequest
) -> dict[str, Any]:
    return outer_request(user_id, kind, policy_id, body)


def _legacy(
    user_id: UUID,
    kind: SourceKind,
    policy_id: UUID,
    body: ConfirmReviewedChangeRequest,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    return legacy_request(user_id, kind, policy_id, body, configuration)


def _verify_lifecycle_original(
    fresh: MultiTemplatePreviewResponse,
    body: ConfirmReviewedChangeRequest,
    legacy: dict[str, Any],
    receipt: dict[str, Any],
) -> None:
    """Check the original persisted lifecycle key, not a later equal configuration."""
    kind = fresh.source_kind
    history = fresh.source_originals["mvp_history" if kind == "MVP_POLICY" else "full_history"]
    versions = [row for row in history["versions"] if row["id"] == str(fresh.expected_version_id)]
    if len(versions) != 1:
        raise ValueError("POST_COMMIT_ORIGINAL_VERSION_MISSING")
    version = versions[0]
    if (
        version["user_id"] != str(fresh.user_id)
        or version["policy_id"] != str(fresh.policy_id)
        or version["configuration"] != fresh.before_configuration
        or version["content_hash"] != body.reviewed_configuration_hash
    ):
        raise ValueError("POST_COMMIT_ORIGINAL_VERSION_DIFFERS")
    if kind == "MVP_POLICY":
        confirmation = version["confirmation"]
        if (
            confirmation.get("request_key") != "change:" + body.idempotency_key
            or confirmation.get("request_hash") != configuration_hash(legacy)
            or version["impact_analysis"].get("lifecycle_result") != receipt
        ):
            raise ValueError("POST_COMMIT_ORIGINAL_MVP_KEY_AND_RECEIPT_DIFFERS")
    else:
        commands = [row for row in history["commands"] if row["id"] == receipt["command_id"]]
        if len(commands) != 1:
            raise ValueError("POST_COMMIT_ORIGINAL_FULL_COMMAND_MISSING")
        command = commands[0]
        if (
            command["request"] != legacy
            or command["request_hash"] != configuration_hash(legacy)
            or command["idempotency_key"] != body.idempotency_key
            or command["result"] != receipt
            or command["user_id"] != str(fresh.user_id)
            or command["policy_id"] != str(fresh.policy_id)
            or command["epoch_id"] != str(body.expected_epoch_id)
            or command["version_id"] != str(fresh.expected_version_id)
        ):
            raise ValueError("POST_COMMIT_ORIGINAL_FULL_KEY_AND_RECEIPT_DIFFERS")


def _financial_scope_equal(
    reviewed: MultiTemplatePreviewResponse, actual: MultiTemplatePreviewResponse
) -> bool:
    """Compare every financial value and all 1098 points, retaining both display notes.

    A hypothesis and an actual reading have different provenance labels. Those
    labels stay in their immutable originals and hashes, but are not money or
    constraints. The current-review source/permission guards still use the exact
    original impact_value, including notes, before committing a new version.
    """
    expected = impact_value(reviewed, after=True)
    current = impact_value(actual, after=False)
    for value in (expected, current):
        curve = value.get("curve")
        if curve is not None:
            curve.pop("calculation_notes")
    return expected == current


def _readback(
    engine: Engine,
    user_id: UUID,
    kind: SourceKind,
    policy_id: UUID,
    body: ConfirmReviewedChangeRequest,
    record: ReviewedChangeRecord,
    legacy: dict[str, Any],
    receipt: dict[str, Any],
    now: datetime,
) -> ReviewCommitResponse:
    version_id = None
    fresh = None
    actual_hash = None
    reasons: list[str] = []
    changed: list[str] = []
    matched = False
    try:
        version_id = UUID(
            receipt["version_id"] if kind == "FULL_POLICY" else receipt["current_version_id"]
        )
        fresh, basis = _capture(
            engine,
            user_id,
            kind,
            policy_id,
            ReviewRequest(
                expected_version_id=version_id,
                expected_epoch_id=body.expected_epoch_id,
                configuration=record.preview.after_configuration,
                idempotency_key=body.idempotency_key,
            ),
            now,
        )
        actual_hash = fresh.current_configuration_hash
        _verify_lifecycle_original(fresh, body, legacy, receipt)
        changed = sorted(
            name
            for name in set(record.source_basis.tables) | set(basis.tables)
            if record.source_basis.tables.get(name) != basis.tables.get(name)
        )
        matched = (
            actual_hash == record.preview.candidate_configuration_hash
            and fresh.before_configuration == record.preview.after_configuration
            and coverage(fresh)[2]
            and _financial_scope_equal(record.preview, fresh)
        )
        if not matched:
            reasons.append("POST_COMMIT_ACTUAL_SCOPE_NOT_EQUAL_TO_REVIEWED_CANDIDATE")
    except Exception as cause:
        reasons.append(
            "POST_COMMIT_READBACK_MISSING:" + str(getattr(cause, "code", type(cause).__name__))
        )
    old = record.preview.financial_impact.before
    actual = fresh.financial_impact.before if fresh else None
    return ReviewCommitResponse(
        user_id=user_id,
        source_kind=kind,
        policy_id=policy_id,
        idempotency_key=body.idempotency_key,
        original_request=body,
        outer_request_hash=configuration_hash(_outer(user_id, kind, policy_id, body)),
        legacy_request_hash=configuration_hash(legacy),
        commit_state="COMMITTED",
        status="COMMITTED_REVIEW_SCOPE_MATCHED" if matched else "COMMITTED_BUT_FINANCIAL_UNKNOWN",
        lifecycle_receipt=receipt,
        actual_version_id=version_id,
        actual_configuration_hash=actual_hash,
        reviewed_preview=record.preview,
        actual_readback=fresh,
        covered_scopes=record.covered_scopes,
        uncovered_items=record.uncovered_items,
        changed_source_tables=changed,
        actual_delta_safe_idle_cents=actual.safe_idle_cents - old.safe_idle_cents
        if actual and old and actual.safe_idle_cents is not None and old.safe_idle_cents is not None
        else None,
        actual_delta_minimum_margin_cents=actual.minimum_margin_cents - old.minimum_margin_cents
        if actual
        and old
        and actual.minimum_margin_cents is not None
        and old.minimum_margin_cents is not None
        else None,
        reasons=reasons,
    )


def confirm_reviewed_change(
    engine: Engine,
    user_id: UUID,
    kind: SourceKind,
    policy_id: UUID,
    body: ConfirmReviewedChangeRequest,
    now: datetime,
    principal: LocalActorPrincipal,
) -> ReviewCommitResponse:
    now = _now(now)
    _principal(principal, user_id, now)
    outer = _outer(user_id, kind, policy_id, body)
    run_id = identity(user_id, "CONFIRM", body.idempotency_key)
    with Session(engine) as write, write.begin():
        _user(write, user_id)
        from app.db.models import DecisionRun

        if write.get(DecisionRun, run_id) is not None:
            trace = _original(write, user_id, run_id, now)
            if trace.inputs.get("outer_request") != outer:
                raise _fail("IDEMPOTENCY_CONFLICT")
            receipt = trace.inputs["lifecycle_receipt"]
            legacy = trace.inputs["legacy_request"]
            record = read_review(engine, user_id, body.review_id, now)
        else:
            record = read_review(engine, user_id, body.review_id, now)
            if (record.source_kind, record.policy_id) != (kind, policy_id):
                raise _fail("ORIGINAL_REVIEW_SCOPE_DIFFERS")
            fresh, basis = _capture(
                engine,
                user_id,
                kind,
                policy_id,
                ReviewRequest(
                    expected_version_id=body.expected_version_id,
                    expected_epoch_id=body.expected_epoch_id,
                    configuration=body.configuration,
                    idempotency_key=body.idempotency_key,
                ),
                now,
            )
            try:
                require_current_review(record, body, basis, fresh, now)
            except (TypeError, ValueError) as cause:
                raise _fail("STALE_OR_UNSUPPORTED_FINANCIAL_REVIEW") from cause
            legacy = _legacy(user_id, kind, policy_id, body, fresh.after_configuration)
            if kind == "FULL_POLICY":
                receipt = change_full_policy(
                    write, user_id, policy_id, FullChangeRequest.model_validate(legacy["body"]), now
                ).model_dump(mode="json")
            else:
                receipt = change_policy(
                    write,
                    user_id,
                    policy_id,
                    body.expected_version_id,
                    fresh.after_configuration,
                    body.reviewed_configuration_hash,
                    True,
                    body.reason,
                    body.idempotency_key,
                    now,
                ).model_dump(mode="json")
            record_trace(
                write,
                build_trace(
                    run_id=run_id,
                    user_id=user_id,
                    phase="EVALUATION",
                    as_of=now,
                    parent_run_id=record.review_id,
                    algorithm_versions={MARKER: ALGORITHM},
                    sources=[],
                    policies=[],
                    constraints=[],
                    candidates=[],
                    inputs={
                        "review_phase": "CONFIRM",
                        "user_id": str(user_id),
                        "request": body.model_dump(mode="json"),
                        "actor": principal.model_dump(mode="json"),
                        "outer_request": outer,
                        "outer_request_hash": configuration_hash(outer),
                        "legacy_request": legacy,
                        "legacy_request_hash": configuration_hash(legacy),
                        "lifecycle_receipt": receipt,
                    },
                    outcome={
                        "lifecycle_receipt": receipt,
                        "bank_authority": False,
                        "financial_readback_status": "NOT_READ_AFTER_COMMIT",
                    },
                ),
            )
    return _readback(engine, user_id, kind, policy_id, body, record, legacy, receipt, now)


def lookup_reviewed_change(
    engine: Engine, user_id: UUID, key: str, now: datetime
) -> ReviewCommandLookup:
    from app.db.models import DecisionRun

    with _reader(engine) as read:
        run_id = identity(user_id, "CONFIRM", key)
        if read.get(DecisionRun, run_id) is None:
            return ReviewCommandLookup(
                status="NOT_FOUND_NOT_FINAL", user_id=user_id, idempotency_key=key
            )
        trace = _original(read, user_id, run_id, _now(now))
        raw = trace.inputs
        body = ConfirmReviewedChangeRequest.model_validate_json(json.dumps(raw["request"]))
        outer = raw["outer_request"]
        if body.idempotency_key != key or outer["user_id"] != str(user_id):
            raise _fail("ORIGINAL_COMMAND_KEY_AND_OWNER_DIFFERS")
        record = read_review(engine, user_id, body.review_id, now)
        response = _readback(
            engine,
            user_id,
            outer["source_kind"],
            UUID(outer["policy_id"]),
            body,
            record,
            raw["legacy_request"],
            raw["lifecycle_receipt"],
            now,
        )
        return ReviewCommandLookup(
            status="RECORDED",
            user_id=user_id,
            idempotency_key=key,
            original_request=body,
            response=response,
        )
