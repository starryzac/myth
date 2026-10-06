"""Pure dependency selection guards; no database or application engine imports."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[4]
SELECT = runpy.run_path(str(ROOT / "scripts/select_verification.py"))["select_verification"]


def configuration() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.safe_load((ROOT / "docs/spec/verification-map.yaml").read_text(encoding="utf-8")),
    )


def test_audit_read_selects_original_negatives_and_actual_post_consumer() -> None:
    plan = SELECT(["apps/api/app/services/audit_chain.py"], configuration())
    nodes = plan["stages"]["integration_pg"]
    assert any("test_next_request_rejects_nonhead_tampering" in node for node in nodes)
    assert any("test_ordinary_owner_dml_cannot_rewrite_or_remove_history" in node for node in nodes)
    assert any(
        "test_http_preparation_confirmation_execution_and_receipt_roundtrip" in n for n in nodes
    )
    assert plan["status"] == "SELECTED_NOT_RUN"
    assert not plan["executed"]


def test_unknown_financial_file_requires_scope_review_with_conservative_consumers() -> None:
    plan = SELECT(["apps/api/app/services/future_financial_module.py"], configuration())
    assert plan["status"] == "NEEDS_SCOPE_REVIEW"
    assert plan["unrecognized_files"]
    assert plan["stages"]["integration_pg"]
    assert not plan["full_suite_selected"]


def test_execution_projection_is_known_receipt_consumer_with_all_original_guards() -> None:
    plan = SELECT(["apps/api/app/services/execution_projection.py"], configuration())
    assert plan["status"] == "SELECTED_NOT_RUN"
    assert not plan["unrecognized_files"]
    assert (
        plan["stages"]["integration_pg"]
        == SELECT(["apps/api/app/services/recovery_receipt_integrity.py"], configuration())[
            "stages"
        ]["integration_pg"]
    )
    assert any(
        "test_completed_receipt_read_and_replay" in n for n in plan["stages"]["integration_pg"]
    )


def test_selection_deduplicates_and_places_quick_checks_first() -> None:
    plan = SELECT(
        ["scripts/tasks.py", "scripts/tasks.py", "scripts/generate_openapi.py"], configuration()
    )
    assert len(plan["changed_files"]) == 2
    assert plan["stages"]["quick_pure"] == ["apps/api/app/tests/test_task_orchestration.py"]
    assert plan["stage_order"][:2] == ["quick_static", "quick_pure"]
    assert not plan["stages"]["integration_pg"]


def test_documentation_does_not_silently_cover_unrecognized_financial_file() -> None:
    plan = SELECT(
        ["docs/spec/product-spec.md", "apps/api/app/domain/new_money.py"], configuration()
    )
    assert plan["status"] == "NEEDS_SCOPE_REVIEW"
    assert plan["unrecognized_files"] == ["apps/api/app/domain/new_money.py"]
    assert plan["uncovered"]
