"""Real original-fact guards using the existing generated migration database fixture."""

import pytest
from alembic.config import Config
from app.tests.external_sql_guard_cases import run_guards
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_external_originals_and_economic_legs_reject_sql_tampering_without_writes(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, _ = migrated_database
    result = run_guards(engine)
    assert result["actual_salary"]["bank_status"] == "SETTLED"
    assert result["actual_salary"]["projection_status"] == "PROJECTED"
    assert result["actual_salary"]["transaction_id"] is not None
    assert result["bank_economic_posting_count"] == 2 and result["case_count"] == 5
    assert result["projection_diagnostics_tested_as_economic_originals"] is False
    expected = {
        "original_request_coherent_rehash": ("EXECUTE", "EXTERNAL_FACT_ORIGINAL_IMMUTABLE"),
        "original_amount_coherent_rehash": ("EXECUTE", "EXTERNAL_FACT_ORIGINAL_IMMUTABLE"),
        "original_first_key": ("EXECUTE", "EXTERNAL_FACT_ORIGINAL_IMMUTABLE"),
        "delete_actual_economic_cash_leg": ("COMMIT", "EXTERNAL_ECONOMIC_POSTING_SET_INVALID"),
        "update_actual_economic_cash_leg": (
            "EXECUTE",
            "Bank postings are immutable economic facts",
        ),
    }
    cases = result["cases"]
    assert {case["case"] for case in cases} == set(expected)
    baseline_hash = cases[0]["before_data_sha256"]
    baseline_counts, baseline_tables = cases[0]["table_counts"], cases[0]["table_sha256"]
    assert "alembic_version" in baseline_counts and "external_bank_facts" in baseline_counts
    assert baseline_counts["external_bank_facts"] == 1
    for case in cases:
        assert case["ordinary_dml_rejected"] is True and case["sqlstate"] == "23514"
        assert (case["rejected_at"], case["message_primary"]) == expected[case["case"]]
        assert case["all_actual_tables_equal"] is True
        assert case["before_data_sha256"] == case["after_data_sha256"] == baseline_hash
        assert case["table_counts"] == baseline_counts
        assert case["table_sha256"] == baseline_tables
        if case["case"] == "update_actual_economic_cash_leg":
            assert case["guard_original_revision"] == "0003_simulated_bank (retained by 0007)"
        else:
            assert case["guard_original_revision"] == "0007_external_bank_facts"
