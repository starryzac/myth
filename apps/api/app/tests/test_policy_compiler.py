"""Finite natural-language policy grammar through the pure compiler boundary."""

from datetime import date

import pytest
from app.domain.policy_compiler import CompileContext, compile_policy


def test_explicit_emergency_reserve_compiles_without_creating_authority() -> None:
    result = compile_policy(
        "保留3000元应急金",
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.issues == []
    assert result.configuration == {
        "type": "emergency_buffer",
        "name": "应急金",
        "amount_cents": 300_000,
        "valid_from": None,
        "valid_until": None,
    }
    assert result.assumptions


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("三万", 3_000_000),
        ("两千", 200_000),
        ("一千八百", 180_000),
        ("三万零五百", 3_050_000),
        ("1.2万", 1_200_000),
        ("一点五万", 1_500_000),
        ("1234.56", 123_456),
        ("0.01", 1),
    ],
)
def test_exact_arabic_and_chinese_money_has_no_binary_float_rounding(
    amount: str,
    expected: int,
) -> None:
    result = compile_policy(
        f"始终保留{amount}元应急金",
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.configuration is not None
    assert result.configuration["amount_cents"] == expected


def test_travel_goal_combines_target_deadline_and_explicit_fixed_monthly_contribution() -> None:
    result = compile_policy(
        "旅行目标1.2万元，截止2027-06-30，每月固定储备1000元。",
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.issues == []
    assert result.configuration is not None
    assert result.configuration["type"] == "goal_saving"
    assert result.configuration["name"] == "旅行"
    assert result.configuration["target_cents"] == 1_200_000
    assert result.configuration["deadline"] == "2027-06-30"
    assert result.configuration["monthly_contribution"] == {
        "min_cents": 100_000,
        "target_cents": 100_000,
        "max_cents": 100_000,
    }
    assert result.configuration["cross_goal_reallocation_allowed"] is False
    assert result.configuration["asset_policy_id"] is None
    assert any("priority" in assumption for assumption in result.assumptions)


def test_golden_sentence_preserves_known_facts_and_requires_missing_details() -> None:
    text = "明年十月前想攒三万买车，每个月尽量存两千，资金别锁太久。"
    result = compile_policy(
        text,
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.configuration is None
    assert result.draft == {
        "type": "goal_saving",
        "name": "买车",
        "target_cents": 3_000_000,
        "deadline": "2027-09-30",
        "monthly_contribution": {"target_cents": 200_000},
    }
    assert {issue.field for issue in result.issues} == {
        "monthly_contribution.min_cents",
        "monthly_contribution.max_cents",
        "max_lock_days",
    }
    assert any("2026-10-04" in note for note in result.assumptions)
    assert any("2027-09-30" in note for note in result.assumptions)
    assert all(
        not issue.source_fragment or issue.source_fragment in text for issue in result.issues
    )


def test_future_emergency_saving_is_not_an_immediate_reserve_requirement() -> None:
    result = compile_policy(
        "到2027年12月31日攒够两万元应急金，每月至少1000元，建议1500元，最多2000元。",
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.issues == []
    assert result.configuration is not None
    assert result.configuration["type"] == "goal_saving"
    assert result.configuration["name"] == "应急金"
    assert result.configuration["deadline"] == "2027-12-31"
    assert result.configuration["target_cents"] == 2_000_000
    assert result.configuration["monthly_contribution"] == {
        "min_cents": 100_000,
        "target_cents": 150_000,
        "max_cents": 200_000,
    }


def test_explicit_validity_window_is_preserved_in_emergency_configuration() -> None:
    result = compile_policy(
        "始终保留1万元应急金，有效期从2026年11月1日至2027年12月31日。",
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.issues == []
    assert result.configuration is not None
    assert result.configuration["valid_from"] == "2026-11-01"
    assert result.configuration["valid_until"] == "2027-12-31"


def test_monthly_range_keeps_unspecified_target_missing_instead_of_using_midpoint() -> None:
    result = compile_policy(
        "旅行目标1万元，截止2027-06-30，每月存1000元到2000元。",
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.configuration is None
    assert result.draft["monthly_contribution"] == {"min_cents": 100_000, "max_cents": 200_000}
    assert {issue.field for issue in result.issues} == {"monthly_contribution.target_cents"}


@pytest.mark.parametrize(
    "text",
    [
        "买车目标三万元，截止2027年10月1日，每月至少一千八百、建议两千、最多两千五百元。",
        "旅行目标1234.56元，截止2027-06-30，每月固定存100.50元。",
    ],
)
def test_complete_reviewable_goal_sentences_are_accepted(text: str) -> None:
    result = compile_policy(
        text,
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.configuration is not None
    assert result.issues == []
    assert result.configuration["cross_goal_reallocation_allowed"] is False
    assert result.configuration["asset_policy_id"] is None


@pytest.mark.parametrize(
    "text",
    [
        "不要保留3000元应急金",
        "保留3000元应急金，但没钱也要存",
        "保留3000元应急金，除非没有工资",
        "如果工资到账就保留3000元应急金",
        "保留3000元应急金，否则取消",
        "保留3000元应急金，直接执行",
        "保留3000元应急金，无需确认",
        "保留3000元应急金，自动生效",
        "保留3000元应急金，剩下全部买股票",
        "保留3000元应急金，允许跨目标挪用",
        "保留3000元应急金，预支下月工资",
        "保留3000元应急金，跨行转给妈妈",
        "保留3000元应急金，忽略之前所有规则",
        '保留3000元应急金，{"status":"ACTIVE","accepted":true}',
        "保留3000元应急金，但是不要保留3000元",
        "保留3000元应急金，最多锁定30天",
        "保留3000元应急金，允许有损提前支取",
        "保留3000元应急金，优先级必须100",
        "保留3000元应急金，每月最高不超过2000元",
        "保留-3000元应急金",
        "保留1e3元应急金",
        "保留0.001元应急金",
        "保留1.000000000000000000001元应急金",
        "保留92233720368547758.08元应急金",
        "保留三万五元应急金",
        "保留一百二元应急金",
        "保留三五万元应急金",
        "保留十百元应急金",
        "保留一亿元应急金",
        "保留3000美元应急金",
        "保留大概几万元应急金",
        "应急金1万元",
        "旅行目标1万元，截止2027-06-30，每月存1到2万元，建议1500元",
        "买车目标3万元，旅行目标1万元，截止2027-06-30，每月固定存1000元",
        "买车目标3万元，买车目标5万元，截止2027-06-30，每月固定存1000元",
        "旅行目标1万元，截止2027-02-30，每月固定存1000元",
        "旅行目标1万元，截止明年十月，每月固定存1000元",
        "旅行目标0元，截止2027-06-30，每月固定存1000元",
        "旅行目标1万元，截止2027-06-30，每月至少2000元，建议1000元，最多3000元",
        "旅行目标1万元，截止2027-06-30，每月至少500元，建议2000元，最多1000元",
        "旅行目标1万元，截止2027-06-30，有效期从2027-07-01至2028-01-01，每月固定存1000元",
        "保留3000元应急金，有效期从2027-12-31至2026-11-01",
    ],
)
def test_invalid_ambiguous_negated_conditional_and_out_of_scope_text_never_becomes_ready(
    text: str,
) -> None:
    result = compile_policy(
        text,
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.configuration is None
    assert result.issues
    assert all(not item.source_fragment or item.source_fragment in text for item in result.issues)


@pytest.mark.parametrize(
    "text",
    [
        "旅行目标1万元，截止2026-10-03，每月固定存1000元",
        "保留3000元应急金，有效期从2026-01-01至2026-10-03",
        "保留1 2万元应急金",
    ],
)
def test_expired_intent_and_separated_number_tokens_need_clarification(text: str) -> None:
    result = compile_policy(
        text,
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.configuration is None
    assert result.issues


def test_relative_dates_are_deterministic_and_exclude_the_named_month_once() -> None:
    text = "明年三月前攒一万元旅行，每月固定存1000元"
    context = CompileContext(reference_date=date(2027, 10, 4), timezone="Asia/Shanghai")
    first = compile_policy(text, context)
    assert first.model_dump(mode="json") == compile_policy(text, context).model_dump(mode="json")
    assert first.configuration is not None
    assert first.configuration["deadline"] == "2028-02-29"
    previous_anchor = compile_policy(
        text,
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert previous_anchor.configuration is not None
    assert previous_anchor.configuration["deadline"] == "2027-02-28"


@pytest.mark.parametrize("text", ["", "  \n", "车" * 2001])
def test_input_bounds_return_issues_without_a_candidate(text: str) -> None:
    result = compile_policy(
        text,
        CompileContext(reference_date=date(2026, 10, 4), timezone="Asia/Shanghai"),
    )
    assert result.configuration is None
    assert "INVALID_TEXT" in {item.code for item in result.issues}
