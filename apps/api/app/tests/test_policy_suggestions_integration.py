"""Root runs this disposable real PG node; no formal financial history is changed."""

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import EvidenceItem, Transaction
from app.domain.pattern_suggestions import PeriodicParameters, SeasonalParameters
from app.main import create_app
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.policy_suggestions import periodic_suggestions, seasonal_suggestions
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import snapshot
from app.tests.test_full_policy_lifecycle_integration import readonly
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_patterns_public_calendar_and_source_refusal_never_write_or_grant(
    boundary_engine: Engine,
) -> None:
    before = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        periodic = periodic_suggestions(session, DEMO_USER_ID, SEED_AS_OF, PeriodicParameters())
        # Original seed confirms essential spending only. Its two rent rows are
        # genuine BANK facts with no actual user category confirmation; they
        # cannot become precise obligations just because this test wants READY.
        rents = list(
            session.scalars(
                select(Transaction).where(
                    Transaction.user_id == DEMO_USER_ID, Transaction.category == "rent"
                )
            )
        )
        assert len(rents) == 2 and all(not row.category_confirmed for row in rents)
        assert not periodic.history_proof.verified
        assert {issue.code for issue in periodic.source_issues} == {"INVALID_CATEGORY_CONFIRMATION"}
        assert periodic.patterns and all(
            item.status == "UNKNOWN" and item.candidate_configuration is None
            for item in periodic.patterns
        )
        assert "RENT" not in {item.kind for item in periodic.patterns}
        assert not periodic.bank_authority and not periodic.hard_protection_changed
        assert periodic.source_evidence_ids and periodic.excluded_transaction_count > 0
        assert (
            periodic_suggestions(session, DEMO_USER_ID, SEED_AS_OF, PeriodicParameters())
            == periodic
        )
        seasonal = seasonal_suggestions(
            session, DEMO_USER_ID, SEED_AS_OF, SeasonalParameters(window_id="CN-2026-NATIONAL_DAY")
        )
        assert seasonal.history_proof.verified
        assert seasonal.suggestion.status == "INSUFFICIENT_HISTORY"
        assert seasonal.suggestion.required_adjustment_cents is None
        assert seasonal.suggestion.candidate_configuration is None
        assert seasonal.public_windows and seasonal.sources
        assert not seasonal.hard_protection_changed and not seasonal.bank_authority
    assert snapshot(boundary_engine) == before
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: boundary_engine
    app.dependency_overrides[get_now] = lambda: SEED_AS_OF
    with TestClient(app) as client:
        actual = client.get("/api/v1/policy-suggestions/periodic")
        assert actual.status_code == 200
        assert actual.json() == periodic.model_dump(mode="json")
        seasonal_http = client.get(
            "/api/v1/policy-suggestions/seasonal", params={"window_id": "CN-2026-NATIONAL_DAY"}
        )
        assert seasonal_http.status_code == 200
        assert seasonal_http.json() == seasonal.model_dump(mode="json")
        assert (
            client.get("/api/v1/policy-suggestions/periodic", params={"facts": "fake"}).status_code
            == 422
        )
    assert snapshot(boundary_engine) == before
    # Change only the generated fixture's coverage status through normal ORM. Original
    # hash bytes are kept; the endpoint must freshly refuse it on the next invocation.
    with Session(boundary_engine) as session, session.begin():
        coverage = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_TRANSACTION_HISTORY_COVERAGE"
            )
        )
        assert coverage is not None
        coverage.status = "UNKNOWN"
    changed = snapshot(boundary_engine)
    with readonly(boundary_engine) as session:
        result = periodic_suggestions(session, DEMO_USER_ID, SEED_AS_OF, PeriodicParameters())
        assert not result.history_proof.verified and result.source_issues
        assert all(item.candidate_configuration is None for item in result.patterns)
        seasonal = seasonal_suggestions(
            session, DEMO_USER_ID, SEED_AS_OF, SeasonalParameters(window_id="CN-2026-NATIONAL_DAY")
        )
        assert not seasonal.history_proof.verified
        assert seasonal.suggestion.proposed_adjustment_cents is None
    assert snapshot(boundary_engine) == changed


def assert_confirmed_patterns(value: object) -> None:
    """Retain the original positive contract for a future real actor-confirmed read.

    Root will call this after the production category-confirmation entry point,
    never after changing seed rows or inserting fabricated evidence directly.
    This helper is not an executed assertion or a PG pass on its own.
    """
    from app.services.policy_suggestions import PeriodicSuggestions

    assert isinstance(value, PeriodicSuggestions)
    assert value.history_proof.verified and value.source_issues == []
    assert {item.kind for item in value.patterns} == {"FIXED_TRANSFER", "RENT", "CREDIT_CARD_BILL"}
    assert all(item.status == "READY" and item.sample_count == 2 for item in value.patterns)
    assert all(item.candidate_configuration is not None for item in value.patterns)
    assert all(
        item.candidate_configuration["auto_execute"] is False
        for item in value.patterns
        if item.candidate_configuration
    )
