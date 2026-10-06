"""Real template, consent, purchase and priced redemption in disposable PostgreSQL.

No successful scenario rewrites a historical position, bank proof or authority.
Negative tests deliberately corrupt original terms or change a quote's status, then
require atomic refusal without additional financial effects or replacement evidence.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.models import (
    Account,
    AssetPosition,
    AssetProduct,
    BankOperation,
    EvidenceItem,
    PolicyProposal,
    SimulatedBankPosting,
    User,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import (
    ConfirmActionRequest,
    PrepareActionRequest,
    PurchaseIntent,
    RedeemIntent,
)
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.demo_console import prepare_demo_template
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.policy_lifecycle import PolicyLifecycleError, confirm_proposal
from app.services.simulated_redemption_quote import issue_fixed_early_quote
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)
PRICE_NOW = NOW + timedelta(seconds=4)


@dataclass(frozen=True)
class FixedPurchase:
    position_id: UUID
    product_id: UUID
    bank_operation_id: UUID
    cash_before_purchase_cents: int


def cash_total(engine: Engine) -> int:
    with Session(engine) as session:
        return sum(
            row.balance_cents
            for row in session.scalars(
                select(Account).where(
                    Account.user_id == DEMO_USER_ID,
                    Account.account_type != "CREDIT_CARD",
                )
            )
        )


@pytest.fixture
def fixed_purchase(demo_engine: Engine) -> FixedPurchase:
    seed_demo(demo_engine)
    before = json.loads(database_snapshot(demo_engine))
    with Session(demo_engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = epoch.id
    template = prepare_demo_template(demo_engine, DEMO_USER_ID, "FIXED_ASSET", epoch_id, NOW)
    assert template.proposal_id is not None and template.evidence_id is not None
    assert template.configuration_hash == configuration_hash(template.configuration)
    with Session(demo_engine) as session, session.begin():
        source = session.get(EvidenceItem, template.evidence_id)
        assert source is not None and source.user_id == DEMO_USER_ID
        assert (
            source.evidence_level == "USER_DECLARED" and source.source_type == "DEMO_EVENT_INTENT"
        )
        assert source.content == {
            "simulation": True,
            "preset_version": "demo-console-v1",
            "user_id": str(DEMO_USER_ID),
            "epoch_id": str(epoch_id),
            "kind": "FIXED_ASSET",
            "phase": "template",
            "admitted_at": NOW.isoformat(),
            "inputs": {
                "configuration": template.configuration,
                "configuration_hash": template.configuration_hash,
            },
        }
        assert source.content_hash == configuration_hash(source.content)
        declaration = session.get(PolicyProposal, template.proposal_id)
        assert declaration is not None and declaration.user_id == DEMO_USER_ID
        assert declaration.source_type == "DEMO_TEMPLATE" and declaration.status == "PROPOSED"
        assert declaration.proposed_configuration == template.configuration
        assert declaration.evidence_ids == [str(source.id)]
        confirmed = confirm_proposal(
            session,
            DEMO_USER_ID,
            template.proposal_id,
            template.configuration_hash,
            True,
            NOW + timedelta(seconds=1),
        )
    cash_before = cash_total(demo_engine)
    prepared = prepare_action(
        demo_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="real-fixed-purchase-for-price",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=confirmed.policy_id),
        ),
        NOW + timedelta(seconds=2),
    )
    assert prepared.autonomy_level == "AUTO_EXECUTE"
    assert prepared.effect.amount_cents == 50000
    assert prepared.effect.position_id is not None and prepared.effect.product_id is not None
    purchased = execute_action(
        demo_engine, DEMO_USER_ID, prepared.action_id, NOW + timedelta(seconds=3)
    )
    assert purchased.status == "SUCCEEDED" and purchased.receipt is not None
    assert purchased.receipt.executed_cents == 50000
    assert purchased.receipt.fee_cents == purchased.receipt.loss_cents == 0
    assert cash_total(demo_engine) == cash_before - 50000
    after = json.loads(database_snapshot(demo_engine))
    positions = {row["id"]: row for row in after["asset_positions"]}
    for original in before["asset_positions"]:
        assert positions[original["id"]] == original
    with Session(demo_engine) as session:
        position = session.get(AssetPosition, prepared.effect.position_id)
        assert position is not None and position.status == "HELD"
        assert position.policy_version_id == confirmed.current_version_id
        assert position.principal_cents == 50000
        product = session.get(AssetProduct, prepared.effect.product_id)
        assert product is not None and product.asset_class == "FIXED_DEPOSIT"
        assert product.early_withdrawal_loss_bps == 20
        assert position.maturity_at == position.purchased_at + timedelta(days=30)
    return FixedPurchase(
        prepared.effect.position_id,
        prepared.effect.product_id,
        purchased.receipt.bank_operation_id,
        cash_before,
    )


def test_real_fixed_price_requires_exact_consent_and_conserves_original_bank_legs_once(
    demo_engine: Engine, fixed_purchase: FixedPurchase
) -> None:
    before_quote = json.loads(database_snapshot(demo_engine))
    quote = issue_fixed_early_quote(
        demo_engine, DEMO_USER_ID, fixed_purchase.position_id, PRICE_NOW
    )
    assert quote.kind == "EARLY_WITHDRAW" and quote.principal_cents == 50000
    assert (quote.fee_cents, quote.loss_cents, quote.net_cents) == (0, 100, 49900)
    assert quote.principal_available_at == PRICE_NOW
    assert quote.expires_at == PRICE_NOW + timedelta(minutes=15)
    after_quote = json.loads(database_snapshot(demo_engine))
    for table, rows in before_quote.items():
        if table != "evidence_items":
            assert after_quote[table] == rows
    evidence = {row["id"]: row for row in after_quote["evidence_items"]}
    for original in before_quote["evidence_items"]:
        assert evidence[original["id"]] == original
    added = evidence.keys() - {row["id"] for row in before_quote["evidence_items"]}
    assert added == {str(quote.quote_id)}
    proof = evidence[str(quote.quote_id)]
    assert proof["evidence_level"] == "BANK_CONFIRMED" and proof["status"] == "VALID"
    assert proof["content"]["quote"] == quote.model_dump(mode="json")
    assert proof["content_hash"] == configuration_hash(proof["content"])
    replay_snapshot = database_snapshot(demo_engine)
    assert (
        issue_fixed_early_quote(
            demo_engine,
            DEMO_USER_ID,
            fixed_purchase.position_id,
            PRICE_NOW + timedelta(microseconds=250_000),
        )
        == quote
    )
    assert database_snapshot(demo_engine) == replay_snapshot
    prepared = prepare_action(
        demo_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="real-fixed-redemption-for-price",
            intent=RedeemIntent(kind="redeem_asset", position_id=fixed_purchase.position_id),
        ),
        PRICE_NOW + timedelta(seconds=1),
    )
    assert prepared.autonomy_level == "ASK_ONCE"
    assert prepared.effect.quote_id == quote.quote_id
    assert prepared.effect.latest_arrival_at == quote.expires_at
    assert prepared.effect.amount_cents == 50000
    assert (prepared.effect.fee_cents, prepared.effect.loss_cents, prepared.effect.net_cents) == (
        0,
        100,
        49900,
    )
    refused = database_snapshot(demo_engine)
    with pytest.raises(PolicyLifecycleError):
        execute_action(
            demo_engine,
            DEMO_USER_ID,
            prepared.action_id,
            PRICE_NOW + timedelta(seconds=1, microseconds=500_000),
        )
    assert database_snapshot(demo_engine) == refused
    confirmed = confirm_action(
        demo_engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        PRICE_NOW + timedelta(seconds=2),
    )
    assert confirmed.effect == prepared.effect and confirmed.effect_hash == prepared.effect_hash
    confirmed_snapshot = json.loads(database_snapshot(demo_engine))
    result = execute_action(
        demo_engine, DEMO_USER_ID, prepared.action_id, PRICE_NOW + timedelta(seconds=3)
    )
    assert result.status == "SUCCEEDED" and result.receipt is not None
    assert result.receipt.executed_cents == 50000
    assert (result.receipt.fee_cents, result.receipt.loss_cents) == (0, 100)
    assert result.receipt.bank_operation_id != fixed_purchase.bank_operation_id
    assert cash_total(demo_engine) == fixed_purchase.cash_before_purchase_cents - 100
    after = json.loads(database_snapshot(demo_engine))
    assert len(after["bank_operations"]) == len(confirmed_snapshot["bank_operations"]) + 1
    assert len(after["action_receipts"]) == len(confirmed_snapshot["action_receipts"]) + 1
    with Session(demo_engine) as session:
        operation = session.get(BankOperation, result.receipt.bank_operation_id)
        assert operation is not None and operation.status == "SETTLED"
        assert operation.operation_type == "REDEEM_ASSET"
        legs = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == operation.id,
                    SimulatedBankPosting.ledger_dimension == "ECONOMIC",
                )
            )
        )
        assert sorted(row.delta_cents for row in legs) == [-50000, 100, 49900]
        assert {(row.ledger_key, row.delta_cents) for row in legs} == {
            (f"POSITION:{fixed_purchase.position_id}", -50000),
            (f"CASH:{prepared.effect.destination_account_id}", 49900),
            (f"LOSS:{DEMO_USER_ID}", 100),
        }
        assert sum(row.delta_cents for row in legs) == 0
        assert {row.id for row in legs} <= set(result.receipt.posting_ids)
        position = session.get(AssetPosition, fixed_purchase.position_id)
        assert position is not None and position.status == "REDEEMED"
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
    done = database_snapshot(demo_engine)
    assert (
        execute_action(
            demo_engine, DEMO_USER_ID, prepared.action_id, PRICE_NOW + timedelta(seconds=4)
        ).receipt
        == result.receipt
    )
    assert database_snapshot(demo_engine) == done


def test_foreign_owner_and_unaware_clock_cannot_publish_any_price(
    demo_engine: Engine, fixed_purchase: FixedPurchase
) -> None:
    foreign_id = uuid4()
    with Session(demo_engine) as session, session.begin():
        session.add(
            User(
                id=foreign_id,
                external_ref=str(foreign_id),
                display_name="foreign",
                is_simulated=True,
            )
        )
    before = database_snapshot(demo_engine)
    with pytest.raises(PolicyLifecycleError) as foreign:
        issue_fixed_early_quote(demo_engine, foreign_id, fixed_purchase.position_id, PRICE_NOW)
    assert foreign.value.status_code == 404
    assert database_snapshot(demo_engine) == before
    with pytest.raises(PolicyLifecycleError) as clock:
        issue_fixed_early_quote(
            demo_engine, DEMO_USER_ID, fixed_purchase.position_id, PRICE_NOW.replace(tzinfo=None)
        )
    assert clock.value.code == "INVALID_CLOCK"
    assert database_snapshot(demo_engine) == before


def test_changed_original_early_contract_is_rejected_without_publishing_a_price(
    demo_engine: Engine, fixed_purchase: FixedPurchase
) -> None:
    # Deliberate negative corruption only. It is never used to make a purchase,
    # quote or redemption succeed and does not rewrite position/proof/authority.
    with Session(demo_engine) as session, session.begin():
        product = session.get(AssetProduct, fixed_purchase.product_id)
        assert product is not None
        product.early_withdrawal_rule = {**product.early_withdrawal_rule, "allowed": False}
    corrupted = database_snapshot(demo_engine)
    with pytest.raises(PolicyLifecycleError) as error:
        issue_fixed_early_quote(demo_engine, DEMO_USER_ID, fixed_purchase.position_id, PRICE_NOW)
    assert error.value.status_code == 409
    assert database_snapshot(demo_engine) == corrupted


def test_expired_original_price_is_not_repriced_or_rewritten(
    demo_engine: Engine, fixed_purchase: FixedPurchase
) -> None:
    quote = issue_fixed_early_quote(
        demo_engine, DEMO_USER_ID, fixed_purchase.position_id, PRICE_NOW
    )
    before = database_snapshot(demo_engine)
    with pytest.raises(PolicyLifecycleError):
        issue_fixed_early_quote(
            demo_engine, DEMO_USER_ID, fixed_purchase.position_id, quote.expires_at
        )
    assert database_snapshot(demo_engine) == before
    with pytest.raises(PolicyLifecycleError):
        prepare_action(
            demo_engine,
            DEMO_USER_ID,
            PrepareActionRequest(
                idempotency_key="expired-real-fixed-quote",
                intent=RedeemIntent(kind="redeem_asset", position_id=fixed_purchase.position_id),
            ),
            quote.expires_at,
        )
    assert database_snapshot(demo_engine) == before


def test_superseded_original_price_is_refused_atomically_without_same_id_insert(
    demo_engine: Engine, fixed_purchase: FixedPurchase
) -> None:
    quote = issue_fixed_early_quote(
        demo_engine, DEMO_USER_ID, fixed_purchase.position_id, PRICE_NOW
    )
    with Session(demo_engine) as session, session.begin():
        proof = session.get(EvidenceItem, quote.quote_id)
        assert proof is not None
        proof.status = "SUPERSEDED"
    before = database_snapshot(demo_engine)
    with pytest.raises(PolicyLifecycleError) as error:
        issue_fixed_early_quote(
            demo_engine, DEMO_USER_ID, fixed_purchase.position_id, PRICE_NOW + timedelta(seconds=1)
        )
    assert error.value.status_code == 409
    assert database_snapshot(demo_engine) == before
