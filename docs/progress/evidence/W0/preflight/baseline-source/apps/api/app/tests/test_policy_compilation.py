"""Persisted compilation proposals, explicit editing and isolated provider boundaries."""

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.models import EvidenceItem, Policy, PolicyProposal, PolicyVersion, User
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.policy_compiler import CompileContext
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.policy_compilation import (
    CompilationResponse,
    compile_candidate,
    revise_compilation,
)
from app.services.policy_lifecycle import PolicyLifecycleError, confirm_proposal
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.integration


@pytest.fixture
def compilation_engine() -> Iterator[Engine]:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            seed_demo(engine)
            yield engine
        finally:
            engine.dispose()


def test_compile_persists_source_and_proposed_configuration_without_authority(
    compilation_engine: Engine,
) -> None:
    with Session(compilation_engine) as session, session.begin():
        result = compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
        assert result.simulation is True
        assert result.user_id == DEMO_USER_ID
        assert result.compilation.reference_date.isoformat() == "2026-10-04"
        assert result.compilation.timezone == "Asia/Shanghai"
        assert result.configuration is not None
        assert result.configuration["type"] == "emergency_buffer"
        assert result.configuration["amount_cents"] == 300000
        assert result.configuration_hash is not None
        assert result.proposal_id is not None
        assert result.proposal_status == "PROPOSED"
        source = session.get(EvidenceItem, result.compilation_id)
        assert source is not None and source.evidence_level == "USER_DECLARED"
        assert source.source_type == "POLICY_COMPILATION"
        assert source.content["text"] == "保留3000元应急金"
        assert source.content["compilation"] == result.compilation.model_dump(mode="json")
        proposal = session.get(PolicyProposal, result.proposal_id)
        assert proposal is not None
        assert str(source.id) in proposal.evidence_ids
        assert session.scalars(select(Policy)).all() == []
        assert session.scalars(select(PolicyVersion)).all() == []


def protected_snapshot(engine: Engine) -> str:
    with engine.connect() as connection:
        snapshot = {
            table.name: [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
            if table.name not in {"policy_proposals", "evidence_items"}
        }
    return json.dumps(snapshot, sort_keys=True, default=str)


def compile_draft(engine: Engine) -> CompilationResponse:
    with Session(engine) as session, session.begin():
        return compile_candidate(session, DEMO_USER_ID, "我要存钱", SEED_AS_OF)


def test_incomplete_draft_is_persisted_without_candidate_or_money_changes(
    compilation_engine: Engine,
) -> None:
    before = protected_snapshot(compilation_engine)
    first = compile_draft(compilation_engine)
    assert first.configuration is None and first.configuration_hash is None
    assert first.proposal_id is None and first.proposal_status is None
    assert first.compilation.issues
    assert compile_draft(compilation_engine) == first
    with Session(compilation_engine) as session:
        assert session.scalars(select(PolicyProposal)).all() == []
        assert (
            len(
                session.scalars(
                    select(EvidenceItem).where(EvidenceItem.source_type == "POLICY_COMPILATION")
                ).all()
            )
            == 1
        )
    assert protected_snapshot(compilation_engine) == before


def test_revision_anchor_replay_and_confirmation_preserve_user_choices(
    compilation_engine: Engine,
) -> None:
    with Session(compilation_engine) as session, session.begin():
        first = compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
        source = session.get(EvidenceItem, first.compilation_id)
        assert source is not None
        original_content = json.dumps(source.content, sort_keys=True)
    before = protected_snapshot(compilation_engine)
    with Session(compilation_engine) as session, session.begin():
        second = revise_compilation(
            session,
            DEMO_USER_ID,
            first.compilation_id,
            {"type": "emergency_buffer", "amount_cents": 400000},
            SEED_AS_OF + timedelta(days=1),
        )
        assert second.compilation == first.compilation
        repeated = revise_compilation(
            session,
            DEMO_USER_ID,
            first.compilation_id,
            {"type": "emergency_buffer", "amount_cents": 400000},
            SEED_AS_OF + timedelta(days=1),
        )
        assert repeated == second
        replay = compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
        assert replay.proposal_id == first.proposal_id and replay.proposal_status == "EXPIRED"
        assert first.configuration is not None
        replay_edit = revise_compilation(
            session,
            DEMO_USER_ID,
            first.compilation_id,
            first.configuration,
            SEED_AS_OF + timedelta(days=1),
        )
        assert (
            replay_edit.proposal_id == first.proposal_id
            and replay_edit.proposal_status == "EXPIRED"
        )
        current = session.get(PolicyProposal, second.proposal_id)
        assert current is not None and current.status == "PROPOSED"
        source = session.get(EvidenceItem, first.compilation_id)
        assert source is not None and json.dumps(source.content, sort_keys=True) == original_content
        assert len(current.evidence_ids) == 2
    assert protected_snapshot(compilation_engine) == before
    with Session(compilation_engine) as session, session.begin():
        assert second.proposal_id is not None and second.configuration_hash is not None
        confirm_proposal(
            session,
            DEMO_USER_ID,
            second.proposal_id,
            second.configuration_hash,
            True,
            SEED_AS_OF + timedelta(days=1),
        )
        with pytest.raises(PolicyLifecycleError) as error:
            revise_compilation(
                session,
                DEMO_USER_ID,
                first.compilation_id,
                {"type": "emergency_buffer", "amount_cents": 500000},
                SEED_AS_OF + timedelta(days=1),
            )
        assert error.value.status_code == 409
        assert len(session.scalars(select(Policy)).all()) == 1
        assert len(session.scalars(select(PolicyVersion)).all()) == 1


@pytest.mark.parametrize("status", ["REJECTED", "EXPIRED"])
def test_closed_revision_stays_closed(compilation_engine: Engine, status: str) -> None:
    draft = compile_draft(compilation_engine)
    with Session(compilation_engine) as session, session.begin():
        result = revise_compilation(
            session,
            DEMO_USER_ID,
            draft.compilation_id,
            {"type": "emergency_buffer", "amount_cents": 1},
            SEED_AS_OF,
        )
        proposal = session.get(PolicyProposal, result.proposal_id)
        assert proposal is not None
        proposal.status = status
    with Session(compilation_engine) as session, session.begin():
        replay = revise_compilation(
            session,
            DEMO_USER_ID,
            draft.compilation_id,
            {"type": "emergency_buffer", "amount_cents": 1},
            SEED_AS_OF,
        )
        assert replay.proposal_id == result.proposal_id and replay.proposal_status == status


def test_concurrent_compile_and_revision_are_unique(compilation_engine: Engine) -> None:
    barrier = Barrier(2)

    def run_compile() -> CompilationResponse:
        barrier.wait(timeout=10)
        with Session(compilation_engine) as session, session.begin():
            return compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            f.result(timeout=30)
            for f in [executor.submit(run_compile), executor.submit(run_compile)]
        ]
    assert results[0] == results[1]
    compilation_id = results[0].compilation_id
    barrier = Barrier(2)

    def run_revision() -> CompilationResponse:
        barrier.wait(timeout=10)
        with Session(compilation_engine) as session, session.begin():
            return revise_compilation(
                session,
                DEMO_USER_ID,
                compilation_id,
                {"type": "emergency_buffer", "amount_cents": 400000},
                SEED_AS_OF,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        revised = [
            f.result(timeout=30)
            for f in [executor.submit(run_revision), executor.submit(run_revision)]
        ]
    assert revised[0] == revised[1]
    with Session(compilation_engine) as session:
        assert len(session.scalars(select(PolicyProposal)).all()) == 2
        assert (
            len(
                session.scalars(
                    select(EvidenceItem).where(
                        EvidenceItem.source_type == "POLICY_COMPILATION_EDIT"
                    )
                ).all()
            )
            == 1
        )


@pytest.mark.parametrize("mutation", ["hash", "future", "conflicted", "wrong_anchor", "other_user"])
def test_revision_requires_owned_current_original_source(
    compilation_engine: Engine, mutation: str
) -> None:
    draft = compile_draft(compilation_engine)
    caller_id = DEMO_USER_ID
    with Session(compilation_engine) as session, session.begin():
        source = session.get(EvidenceItem, draft.compilation_id)
        assert source is not None
        if mutation == "hash":
            source.content = {**source.content, "text": "被替换的原文"}
        elif mutation == "wrong_anchor":
            source.content = {**source.content, "reference_date": "2099-01-01"}
            source.content_hash = configuration_hash(source.content)
        elif mutation == "future":
            source.observed_at = SEED_AS_OF + timedelta(seconds=1)
        elif mutation == "conflicted":
            source.status = "CONFLICTED"
        else:
            caller_id = uuid4()
            session.add(User(id=caller_id, external_ref=str(caller_id), display_name="Other"))
    with Session(compilation_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            revise_compilation(
                session,
                caller_id,
                draft.compilation_id,
                {"type": "emergency_buffer", "amount_cents": 400000},
                SEED_AS_OF,
            )
        assert error.value.status_code == (404 if mutation == "other_user" else 422)
        assert session.scalars(select(PolicyProposal)).all() == []


GOAL: dict[str, Any] = {
    "type": "goal_saving",
    "target_cents": 3000000,
    "deadline": "2027-09-30",
    "monthly_contribution": {"min_cents": 100000, "target_cents": 200000, "max_cents": 300000},
}


@pytest.mark.parametrize(
    "configuration",
    [
        {**GOAL, "status": "ACTIVE"},
        {**GOAL, "accepted": True},
        {**GOAL, "target_cents": 1.5},
        {**GOAL, "cross_goal_reallocation_allowed": True},
        {**GOAL, "deadline": "2026-10-02"},
        {**GOAL, "valid_until": "2026-10-02"},
        {"type": "emergency_buffer", "amount_cents": 1, "auto_execute": True},
        {
            "type": "recurring_obligation",
            "payee_id": "test",
            "due_day": 1,
            "amount_rule": {"kind": "exact", "amount_cents": 1},
        },
    ],
)
def test_edits_reject_authority_out_of_scope_and_past_dates(
    compilation_engine: Engine, configuration: dict[str, Any]
) -> None:
    draft = compile_draft(compilation_engine)
    with Session(compilation_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            revise_compilation(
                session, DEMO_USER_ID, draft.compilation_id, configuration, SEED_AS_OF
            )
        assert error.value.status_code == 422
        assert session.scalars(select(PolicyProposal)).all() == []


class FakeProvider:
    def __init__(self, candidate: dict[str, Any]) -> None:
        self.candidate = candidate
        self.calls = 0

    def propose(self, text: str, context: CompileContext) -> dict[str, Any]:
        self.calls += 1
        return self.candidate


def test_provider_disabled_rules_and_missing_provider_fail_explicitly(
    compilation_engine: Engine,
) -> None:
    provider = FakeProvider({"type": "emergency_buffer", "amount_cents": 300000})
    with Session(compilation_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            compile_candidate(
                session,
                DEMO_USER_ID,
                "保留3000元应急金",
                SEED_AS_OF,
                engine="llm",
                provider=provider,
            )
        assert error.value.code == "LLM_DISABLED"
        compile_candidate(
            session,
            DEMO_USER_ID,
            "保留3000元应急金",
            SEED_AS_OF,
            engine="rules",
            llm_enabled=True,
            provider=provider,
        )
        assert provider.calls == 0
        with pytest.raises(PolicyLifecycleError) as error:
            compile_candidate(
                session,
                DEMO_USER_ID,
                "保留3000元应急金",
                SEED_AS_OF,
                engine="llm",
                llm_enabled=True,
            )
        assert error.value.code == "LLM_UNAVAILABLE" and error.value.status_code == 503


def test_enabled_model_is_marked_inferred_and_repeated_without_second_call(
    compilation_engine: Engine,
) -> None:
    provider = FakeProvider({"type": "emergency_buffer", "amount_cents": 300000})
    with Session(compilation_engine) as session, session.begin():
        first = compile_candidate(
            session,
            DEMO_USER_ID,
            "保留3000元应急金",
            SEED_AS_OF,
            engine="llm",
            llm_enabled=True,
            provider=provider,
        )
        second = compile_candidate(
            session,
            DEMO_USER_ID,
            "保留3000元应急金",
            SEED_AS_OF,
            engine="llm",
            llm_enabled=True,
            provider=provider,
        )
        assert first == second and provider.calls == 1
        assert first.proposal_status == "PROPOSED"
        proposal = session.get(PolicyProposal, first.proposal_id)
        assert proposal is not None
        evidence = session.scalars(
            select(EvidenceItem).where(EvidenceItem.id.in_(proposal.evidence_ids))
        ).all()
        assert {item.evidence_level for item in evidence} == {"USER_DECLARED", "MODEL_INFERRED"}
        assert session.scalars(select(Policy)).all() == []


@pytest.mark.parametrize(
    "candidate",
    [
        {"type": "emergency_buffer", "amount_cents": 1, "status": "ACTIVE"},
        {**GOAL, "user_id": str(DEMO_USER_ID), "accepted": True},
        {**GOAL, "cross_goal_reallocation_allowed": True},
        {"type": "emergency_buffer", "amount_cents": 1.1},
    ],
)
def test_model_cannot_smuggle_authority_or_unvalidated_configuration(
    compilation_engine: Engine, candidate: dict[str, Any]
) -> None:
    provider = FakeProvider(candidate)
    with Session(compilation_engine) as session, session.begin():
        result = compile_candidate(
            session,
            DEMO_USER_ID,
            "请处理",
            SEED_AS_OF,
            engine="llm",
            llm_enabled=True,
            provider=provider,
        )
        assert result.configuration is None and result.proposal_id is None
        assert result.compilation.issues
        assert session.scalars(select(PolicyProposal)).all() == []
        assert session.scalars(select(Policy)).all() == []


def test_edit_failure_rolls_back_evidence_and_preserves_previous_candidate(
    compilation_engine: Engine,
) -> None:
    with Session(compilation_engine) as session, session.begin():
        first = compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
    with compilation_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_edit_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected compilation edit failure'; END $$
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER fail_edit BEFORE INSERT ON policy_proposals
            FOR EACH ROW EXECUTE FUNCTION reject_edit_proposal()
        """)
        )
    with Session(compilation_engine) as session, session.begin():
        with pytest.raises(DBAPIError, match="injected compilation edit failure"):
            revise_compilation(
                session,
                DEMO_USER_ID,
                first.compilation_id,
                {"type": "emergency_buffer", "amount_cents": 400000},
                SEED_AS_OF,
            )
        proposal = session.get(PolicyProposal, first.proposal_id)
        assert proposal is not None and proposal.status == "PROPOSED"
        assert (
            session.scalars(
                select(EvidenceItem).where(EvidenceItem.source_type == "POLICY_COMPILATION_EDIT")
            ).all()
            == []
        )


@pytest.mark.parametrize("mutation", ["edited_source", "model_source", "rule_text"])
def test_confirmation_rejects_rehashed_compilation_sources(
    compilation_engine: Engine,
    mutation: str,
) -> None:
    with Session(compilation_engine) as session, session.begin():
        first = compile_candidate(
            session,
            DEMO_USER_ID,
            "保留3000元应急金",
            SEED_AS_OF,
            engine="llm" if mutation == "model_source" else "rules",
            llm_enabled=True,
            provider=FakeProvider({"type": "emergency_buffer", "amount_cents": 300000}),
        )
        proposal = first
        if mutation == "edited_source":
            proposal = revise_compilation(
                session,
                DEMO_USER_ID,
                first.compilation_id,
                {"type": "emergency_buffer", "amount_cents": 400000},
                SEED_AS_OF,
            )
        source = session.get(EvidenceItem, first.compilation_id)
        assert source is not None
        if mutation == "model_source":
            model = session.get(EvidenceItem, UUID(source.content["model_evidence_id"]))
            assert model is not None
            model.content = {
                **model.content,
                "candidate": {"type": "emergency_buffer", "amount_cents": 900000},
            }
            model.content_hash = configuration_hash(model.content)
        elif mutation == "edited_source":
            source.content = {
                **source.content,
                "compilation": {**source.content["compilation"], "assumptions": ["原始解释已更正"]},
            }
            source.content_hash = configuration_hash(source.content)
        else:
            source.content = {**source.content, "text": "保留9000元应急金"}
            source.content_hash = configuration_hash(source.content)
    with Session(compilation_engine) as session, session.begin():
        assert proposal.proposal_id is not None and proposal.configuration_hash is not None
        with pytest.raises(PolicyLifecycleError) as error:
            confirm_proposal(
                session,
                DEMO_USER_ID,
                proposal.proposal_id,
                proposal.configuration_hash,
                True,
                SEED_AS_OF,
            )
        assert error.value.code == "INVALID_EVIDENCE"
        assert session.scalars(select(Policy)).all() == []


def test_human_edit_of_model_candidate_retains_provenance_and_can_be_confirmed(
    compilation_engine: Engine,
) -> None:
    with Session(compilation_engine) as session, session.begin():
        first = compile_candidate(
            session,
            DEMO_USER_ID,
            "应急金",
            SEED_AS_OF,
            engine="llm",
            llm_enabled=True,
            provider=FakeProvider({"type": "emergency_buffer", "amount_cents": 300000}),
        )
        edited = revise_compilation(
            session,
            DEMO_USER_ID,
            first.compilation_id,
            {"type": "emergency_buffer", "amount_cents": 400000},
            SEED_AS_OF,
        )
        proposal = session.get(PolicyProposal, edited.proposal_id)
        assert proposal is not None
        assert len(proposal.evidence_ids) == 3
        assert edited.proposal_id is not None and edited.configuration_hash is not None
        result = confirm_proposal(
            session, DEMO_USER_ID, edited.proposal_id, edited.configuration_hash, True, SEED_AS_OF
        )
        assert result.status == "ACTIVE"


class UnavailableProvider:
    def propose(self, text: str, context: CompileContext) -> dict[str, Any]:
        raise TimeoutError("test provider unavailable")


@pytest.mark.parametrize("kind", ["timeout", "invalid_json"])
def test_provider_failure_is_explicit_and_leaves_no_partial_compilation(
    compilation_engine: Engine,
    kind: str,
) -> None:
    provider = UnavailableProvider() if kind == "timeout" else FakeProvider({"date": SEED_AS_OF})
    with Session(compilation_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            compile_candidate(
                session,
                DEMO_USER_ID,
                "保存",
                SEED_AS_OF,
                engine="llm",
                llm_enabled=True,
                provider=provider,
            )
        assert error.value.code == (
            "LLM_UNAVAILABLE" if kind == "timeout" else "INVALID_LLM_OUTPUT"
        )
        assert (
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.source_type.in_(["POLICY_COMPILATION", "POLICY_COMPILATION_MODEL"])
                )
            ).all()
            == []
        )
        assert session.scalars(select(PolicyProposal)).all() == []


def test_original_anchor_does_not_allow_a_deadline_that_has_passed_today(
    compilation_engine: Engine,
) -> None:
    draft = compile_draft(compilation_engine)
    with Session(compilation_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            revise_compilation(
                session,
                DEMO_USER_ID,
                draft.compilation_id,
                {**GOAL, "deadline": "2026-10-04"},
                SEED_AS_OF + timedelta(days=1),
            )
        assert error.value.code == "PAST_DEADLINE"
        source = session.get(EvidenceItem, draft.compilation_id)
        assert source is not None and source.content["reference_date"] == "2026-10-04"


@pytest.mark.parametrize("operation", ["recompile", "confirm"])
def test_original_compiler_output_cannot_be_rewritten_even_with_new_content_hash(
    compilation_engine: Engine,
    operation: str,
) -> None:
    with Session(compilation_engine) as session, session.begin():
        first = compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
        source = session.get(EvidenceItem, first.compilation_id)
        assert source is not None and first.configuration is not None
        source.content = {
            **source.content,
            "compilation": {
                **source.content["compilation"],
                "configuration": {**first.configuration, "amount_cents": 900000},
            },
        }
        source.content_hash = configuration_hash(source.content)
    with Session(compilation_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            if operation == "recompile":
                compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
            else:
                assert first.proposal_id is not None and first.configuration_hash is not None
                confirm_proposal(
                    session,
                    DEMO_USER_ID,
                    first.proposal_id,
                    first.configuration_hash,
                    True,
                    SEED_AS_OF,
                )
        assert error.value.code == "INVALID_EVIDENCE"
        assert len(session.scalars(select(PolicyProposal)).all()) == 1
        assert session.scalars(select(Policy)).all() == []


def test_duplicate_original_compilations_are_rejected_as_conflict(
    compilation_engine: Engine,
) -> None:
    with Session(compilation_engine) as session, session.begin():
        first = compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
        source = session.get(EvidenceItem, first.compilation_id)
        assert source is not None
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                evidence_level=source.evidence_level,
                source_type=source.source_type,
                source_ref=source.source_ref,
                content=source.content,
                content_hash=source.content_hash,
                valid_from=source.valid_from,
                observed_at=source.observed_at,
                status="VALID",
            )
        )
    with Session(compilation_engine) as session, session.begin():
        with pytest.raises(PolicyLifecycleError) as error:
            compile_candidate(session, DEMO_USER_ID, "保留3000元应急金", SEED_AS_OF)
        assert error.value.code == "COMPILATION_CONFLICT"
        assert error.value.status_code == 409
