"""TOOL_TEST_ONLY baseline rule fixtures; not experiment arms or financial outcomes."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
TOOL = runpy.run_path(str(ROOT / "scripts/mvp_baselines.py"))


def inputs(level: str = "AUTO_EXECUTE") -> object:
    proposal = TOOL["ProductionProposal"](87001, level, "tool-fixture-only", "a" * 64, "b" * 64)
    return TOOL["BaselineInput"](150000, 20003, 50000, 30000, production=proposal)


def test_five_actual_candidate_mechanisms_do_not_relabel_the_same_amount_or_confirmation() -> None:
    facts = inputs()
    decisions = {arm: TOOL["decide"](arm, facts).as_dict() for arm in ("B0", "B1", "B2", "B3", "P")}
    assert [decisions[arm]["proposed_amount_cents"] for arm in ("B0", "B1", "B2", "B3", "P")] == [
        20003,
        100000,
        120000,
        87001,
        87001,
    ]
    assert decisions["B0"]["disposition"] == "MANUAL_CHOICE"
    assert decisions["B3"]["disposition"] == "ASK_EVERY_ACTION"
    assert decisions["P"]["disposition"] == "PROPOSE_AUTONOMOUS"
    assert all(row["execution_mode"] == "MODEL_ONLY" for row in decisions.values())
    assert all(
        row["executed"] is False and row["authority_granted"] is False for row in decisions.values()
    )


def test_naive_rules_do_not_read_or_inherit_production_amount_or_authority() -> None:
    facts = inputs("BLOCKED")
    assert TOOL["decide"]("B1", facts).proposed_amount_cents == 100000
    assert TOOL["decide"]("B2", facts).proposed_amount_cents == 120000
    assert TOOL["decide"]("B1", facts).production_decision_ref is None
    assert TOOL["decide"]("P", facts).disposition == "BLOCKED"
    assert TOOL["decide"]("B3", facts).disposition == "BLOCKED"


def test_b3_and_p_preserve_real_losses_or_advice_levels_without_fabricated_execution() -> None:
    for level in ("ASK_ONCE", "ADVISE_ONLY"):
        facts = inputs(level)
        p = TOOL["decide"]("P", facts)
        b3 = TOOL["decide"]("B3", facts)
        assert p.production_effect_hash == b3.production_effect_hash == "a" * 64
        assert p.disposition == level
        assert b3.disposition == ("ASK_EVERY_ACTION" if level == "ASK_ONCE" else "ADVISE_ONLY")
        assert not b3.executed


def test_static_rules_at_or_below_threshold_remain_zero_without_invented_negative_money() -> None:
    facts = TOOL["BaselineInput"](20000, 0, 20000, 30000)
    for arm in ("B0", "B1", "B2"):
        decision = TOOL["decide"](arm, facts)
        assert decision.proposed_amount_cents == 0
        assert decision.disposition == "NO_ACTION"


def test_original_production_proposal_and_typed_rule_parameters_are_required() -> None:
    facts = TOOL["BaselineInput"](10000, 5000, 1000, 2000)
    for arm in ("B3", "P"):
        with pytest.raises(ValueError, match="original"):
            TOOL["decide"](arm, facts)
    for value in (True, -1, 1.5, 2**63):
        with pytest.raises(ValueError, match="integer"):
            TOOL["BaselineInput"](value, 100, 200, 300)
    with pytest.raises(ValueError, match="SHA256"):
        TOOL["ProductionProposal"](100, "AUTO_EXECUTE", "fixture", "bad", "a" * 64)


def test_metric_draft_retains_all_original_names_and_explicit_unmeasured_boundary() -> None:
    metric = json.loads((ROOT / "docs/spec/mvp-metrics.json").read_text(encoding="utf-8"))
    original = (
        (ROOT / "钱途有界_初版开发计划_Codex执行版.md").read_text(encoding="utf-8").splitlines()
    )
    rows = metric["metrics"]
    assert len(rows) == len({row["id"] for row in rows}) == 14
    assert [
        sum(row["group"] == group for row in rows) for group in ("safety", "efficiency", "audit")
    ] == [5, 5, 4]
    for row in rows:
        assert (
            original[row["source_line"] - 1].strip().removeprefix("- ").rstrip("；。")
            == row["name"]
        )
        assert row["denominator"] and row["oracle"] and row["raw_fields"]
    assert metric["observations"] == []
    assert metric["task_closed"] is False
    assert metric["status"] == "DESIGN_DRAFT_NOT_FROZEN_NOT_RUN"
