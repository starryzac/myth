"""Direct reference, owner, knowledge, denominator and original audit risks; no DB."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.db.models import SimulatedBankPosting
from app.domain import audit_chain as audit
from app.domain.audit_chain_types import AuditEpochTransition, AuditIntent, AuditPayload
from app.domain.full_evidence_graph import (
    KIND_TABLES,
    SHARED_KINDS,
    FullEvidenceGraph,
    GraphInventory,
    GraphOriginal,
    GraphProof,
    Kind,
    build_full_evidence_graph,
    node_key,
)
from app.domain.policy_configuration import configuration_hash
from app.services.full_evidence_graph import (
    _model_map,
    _original_row,
    original_known_at,
    persisted_references,
)
from pydantic import ValidationError

AT = datetime(2026, 10, 6, tzinfo=UTC)
USER = uuid4()
INTEGRITY = GraphProof(state="VERIFIED", check="SYNTHETIC_TEST_ONLY", detail="pure fixture")


def original(kind: Kind, **values: Any) -> GraphOriginal:
    identity = UUID(values.pop("id")) if "id" in values else uuid4()
    raw = {"id": str(identity), "created_at": AT.isoformat(), **values}
    if kind not in SHARED_KINDS | {"USER"}:
        raw.setdefault("user_id", str(USER))
    return GraphOriginal(
        kind=kind, id=identity, known_at=AT, original=raw, row_hash=configuration_hash(raw)
    )


def inventories(rows: list[GraphOriginal]) -> list[GraphInventory]:
    result = []
    for kind, table in KIND_TABLES.items():
        originals = sorted(
            (row.original for row in rows if row.kind == kind), key=lambda row: row["id"]
        )
        result.append(
            GraphInventory(
                kind=cast(Kind, kind),
                table=table,
                owner_scope="SHARED_PRODUCT_CATALOGUE" if kind in SHARED_KINDS else "CURRENT_USER",
                actual_owned_count=len(originals),
                known_count=len(originals),
                captured_count=len(originals),
                complete=True,
                captured_rows_hash=configuration_hash({"rows": originals}),
            )
        )
    return result


def graph(rows: list[GraphOriginal], root: GraphOriginal, **fields: Any) -> FullEvidenceGraph:
    refs, issues = persisted_references(rows)
    return build_full_evidence_graph(
        **{
            "user": USER,
            "root_kind": root.kind,
            "root_id": root.id,
            "now": AT,
            "known_at": AT,
            "inventory": inventories(rows),
            "originals": rows,
            "references": refs,
            "proofs": {},
            "audit": INTEGRITY,
            "bank": INTEGRITY,
            "source_issues": issues,
            **fields,
        }
    )


def test_every_actual_mapped_table_has_an_explicit_denominator() -> None:
    assert set(_model_map()) == set(KIND_TABLES.values())
    assert len(KIND_TABLES) == 35


def test_real_fk_navigation_in_both_directions_keeps_cash_and_ownership_distinct() -> None:
    account = original("ACCOUNT", balance_cents=100003)
    evidence = original(
        "EVIDENCE", source_type="BANK_ACCOUNT_BALANCE", content={"balance_cents": 100003}
    )
    transaction = original("TRANSACTION", account_id=str(account.id), evidence_id=str(evidence.id))
    bill = original("BILL", account_id=str(account.id), evidence_id=str(evidence.id))
    posting = original(
        "POSTING", account_id=str(account.id), delta_cents=-97, ledger_dimension="ECONOMIC"
    )
    rows = [account, evidence, transaction, bill, posting]
    result = graph(rows, account)
    assert result.state == "REFERENCES_RESOLVED" and result.complete_registered_inventory
    assert {row.kind for row in result.nodes} == {
        "ACCOUNT",
        "EVIDENCE",
        "TRANSACTION",
        "BILL",
        "POSTING",
    }
    assert len(graph(rows, evidence).nodes) == 5
    assert graph(rows, posting).expected_node_count == 5
    assert not result.financial_success_inferred and not result.grants_authority
    assert (
        cast(dict[str, Any], next(row.original for row in result.nodes if row.kind == "POSTING"))[
            "delta_cents"
        ]
        == -97
    )


def test_receipt_original_links_action_decision_frozen_evidence_and_policy() -> None:
    evidence = original("EVIDENCE", source_type="USER_DECLARED", content={})
    policy = original("POLICY")
    version = original("POLICY_VERSION", policy_id=str(policy.id), evidence_ids=[str(evidence.id)])
    decision = original(
        "DECISION", policy_version_ids=[str(version.id)], evidence_ids=[str(evidence.id)]
    )
    action = original("ACTION", decision_run_id=str(decision.id), policy_version_id=str(version.id))
    receipt = original("RECEIPT", action_plan_id=str(action.id), executed_cents=200003)
    result = graph([evidence, policy, version, decision, action, receipt], receipt)
    assert result.expected_node_count == 6 and result.state == "REFERENCES_RESOLVED"
    assert all(row.proof.state == "NOT_CHECKED" for row in result.nodes)
    assert not any(row.execution_authority for row in result.nodes)


@pytest.mark.parametrize("field,value", [("user_id", str(uuid4())), ("id", str(uuid4()))])
def test_rehashed_foreign_owner_or_identity_never_enters_view(field: str, value: str) -> None:
    account = original("ACCOUNT")
    transaction = original("TRANSACTION", account_id=str(account.id))
    bad = {**transaction.original, field: value}
    changed = transaction.model_copy(update={"original": bad, "row_hash": configuration_hash(bad)})
    result = graph([account, changed], account)
    assert result.state == "UNKNOWN" and not result.complete_registered_inventory
    assert all(row.kind != "TRANSACTION" for row in result.nodes)


def test_missing_or_future_ref_is_unknown_and_not_taken_from_other_sources() -> None:
    decision = original("DECISION", evidence_ids=[str(uuid4())])
    result = graph([decision], decision)
    assert result.state == "UNKNOWN" and result.expected_edge_count == 1
    assert any(issue.code == "REFERENCE_UNAVAILABLE" for issue in result.issues)
    assert result.displayed_node_count == 1 and result.edges == []
    with pytest.raises(LookupError):
        graph([decision.model_copy(update={"known_at": AT + timedelta(days=1)})], decision)


def test_duplicate_and_missing_table_inventory_cannot_claim_complete() -> None:
    root = original("ACCOUNT")
    inventory = inventories([root])
    for changed in (inventory[:-1], [*inventory[:-1], inventory[0]]):
        result = graph([root], root, inventory=changed)
        assert result.state == "UNKNOWN" and not result.complete_registered_inventory


def test_capacity_keeps_real_table_and_reachable_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domain.full_evidence_graph as domain

    root = original("ACCOUNT")
    transactions = [original("TRANSACTION", account_id=str(root.id)) for _ in range(3)]
    monkeypatch.setattr(domain, "MAX_NODES", 2)
    result = graph([root, *transactions], root)
    assert result.expected_node_count == 4 and result.displayed_node_count == 2
    assert result.state == "UNKNOWN"
    assert (
        next(row for row in result.inventory if row.kind == "TRANSACTION").actual_owned_count == 3
    )


def test_historical_mutable_content_is_not_current_balance_disguised_as_old() -> None:
    root = original("ACCOUNT", balance_cents=999999)
    result = graph([root], root, now=AT + timedelta(days=1))
    assert result.state == "UNKNOWN" and result.nodes[0].original is None
    assert result.nodes[0].row_hash is None
    immutable = original("POSTING", delta_cents=-113)
    old = graph([immutable], immutable, now=AT + timedelta(days=1))
    assert old.nodes[0].original == immutable.original


def test_full_goal_model_root_is_real_evidence_alias_not_guessed_goal_attributes() -> None:
    goal = original("GOAL")
    model = original(
        "EVIDENCE", source_type="FULL_GOAL_MODEL_V1", content={"goal_id": str(goal.id)}
    )
    result = graph([goal, model], model, root_kind="FULL_GOAL_MODEL")
    assert result.root == node_key("EVIDENCE", model.id) and len(result.nodes) == 2
    wrong = original("EVIDENCE", source_type="USER_DECLARED", content={})
    with pytest.raises(LookupError):
        graph([wrong], wrong, root_kind="FULL_GOAL_MODEL")


def test_shared_product_explicit_scope_does_not_grant_permission() -> None:
    product = original("PRODUCT", risk_level=1)
    position = original("POSITION", product_id=str(product.id))
    result = graph([product, position], product)
    node = next(row for row in result.nodes if row.kind == "PRODUCT")
    assert node.owner_scope == "SHARED_PRODUCT_CATALOGUE" and not node.execution_authority
    assert "user_id" not in cast(dict[str, Any], node.original)


def test_knowledge_clock_uses_actual_last_capture_and_ignores_future_maturity() -> None:
    assert original_known_at(
        {
            "created_at": AT.isoformat(),
            "observed_at": (AT + timedelta(hours=1)).isoformat(),
            "available_at": (AT + timedelta(days=90)).isoformat(),
        }
    ) == AT + timedelta(hours=1)
    with pytest.raises(ValueError):
        original_known_at({"created_at": "2026-10-06T00:00:00"})
    with pytest.raises(ValueError):
        original_known_at({"amount_cents": 100})


def test_unknown_integrity_is_visible_and_zero_never_substitutes_missing_proof() -> None:
    root = original("ACCOUNT", balance_cents=0)
    missing = INTEGRITY.model_copy(update={"state": "UNKNOWN", "detail": "missing bank original"})
    result = graph([root], root, bank=missing)
    assert result.state == "UNKNOWN" and result.bank_proof.state == "UNKNOWN"
    assert not result.financial_success_inferred


@pytest.mark.parametrize(
    "field,value", [("actual_owned_count", True), ("known_count", 1.0), ("complete", 1)]
)
def test_inventory_counts_and_flags_remain_strict(field: str, value: object) -> None:
    fields = inventories([])[0].model_dump(mode="python")
    fields[field] = value
    with pytest.raises(ValidationError):
        GraphInventory.model_validate(fields)


def test_actual_typed_audit_roundtrip_and_rehashed_storage_payload_tamper() -> None:
    epoch = uuid4()
    event = audit.build_event(
        AuditIntent(
            user_id=USER,
            event_type="EPOCH_STARTED",
            aggregate_type="EPOCH",
            aggregate_id=epoch,
            correlation_id=epoch,
            idempotency_key="pure-start",
            occurred_at=AT,
            payload=AuditPayload(
                fact_key="pure-start",
                correlation_kind="EPOCH",
                epoch_transition=AuditEpochTransition(kind="INIT", legacy_history=True),
            ),
        ),
        event_id=uuid4(),
        epoch_id=epoch,
        sequence_number=1,
        previous_hash=None,
        observed_at=AT,
        appended_at=AT,
    )
    raw = event.model_dump(mode="json")
    event_row = original(
        "AUDIT_EVENT", **{**raw, "canonical_text": audit.event_canonical_text(event)}
    )
    _, issues = persisted_references([event_row])
    assert issues == []
    changed = {
        **event_row.original,
        "payload": {**event_row.original["payload"], "fact_key": "different"},
    }
    invalid = event_row.model_copy(
        update={"original": changed, "row_hash": configuration_hash(changed)}
    )
    _, issues = persisted_references([invalid])
    assert {row.code for row in issues} == {"INVALID_AUDIT_EVENT_ORIGINAL"}


def test_arbitrary_json_uuid_and_authority_claims_do_not_create_edges() -> None:
    evidence = original(
        "EVIDENCE",
        source_type="MODEL_INFERRED",
        content={"bank_operation_id": str(uuid4()), "success": True, "bank_authority": True},
    )
    refs, issues = persisted_references([evidence])
    assert refs == [] and issues == []
    assert graph([evidence], evidence).nodes[0].proof.state == "NOT_CHECKED"


def test_new_graph_reads_every_posting_column_without_altering_legacy_codec() -> None:
    row = SimulatedBankPosting(
        id=uuid4(),
        user_id=USER,
        created_at=AT,
        ledger_key="CASH:" + str(uuid4()),
        ledger_dimension="ECONOMIC",
        ledger_metadata={},
        sequence_number=1,
        entry_kind="OPENING",
        balance_before_cents=0,
        delta_cents=101137,
        balance_after_cents=101137,
        occurred_at=AT,
    )
    raw = _original_row(row)
    assert raw["ledger_dimension"] == "ECONOMIC" and raw["ledger_metadata"] == {}
    assert "external_fact_id" in raw and "leg_ref" in raw
    assert set(raw) == {column.name for column in row.__table__.columns}
    assert raw["delta_cents"] == 101137


@pytest.mark.parametrize("broken", [None, True, {}, "wrong"])
def test_malformed_full_reference_inventory_is_unknown_not_empty_success(broken: object) -> None:
    row = original("FULL_POLICY_VERSION", impact_analysis={"reference_snapshots": broken})
    _, issues = persisted_references([row])
    assert {issue.code for issue in issues} == {"INVALID_FULL_REFERENCE_ARRAY"}


def test_full_confirmation_original_links_version_policy_and_epoch_without_grant() -> None:
    epoch = original("AUDIT_EPOCH")
    policy = original("FULL_POLICY", epoch_id=str(epoch.id))
    evidence = original(
        "EVIDENCE",
        source_type="FULL_POLICY_CONFIRMATION",
        content={"policy_id": str(policy.id), "epoch_id": str(epoch.id), "bank_authority": False},
    )
    version = original(
        "FULL_POLICY_VERSION",
        policy_id=str(policy.id),
        evidence_ids=[str(evidence.id)],
        confirmation={"confirmation_evidence_id": str(evidence.id)},
        impact_analysis={"reference_snapshots": []},
    )
    raw = {
        **evidence.original,
        "content": {**evidence.original["content"], "version_id": str(version.id)},
    }
    evidence = evidence.model_copy(update={"original": raw, "row_hash": configuration_hash(raw)})
    result = graph([epoch, policy, evidence, version], evidence)
    assert result.expected_node_count == 4 and result.state == "REFERENCES_RESOLVED"
    assert not any(row.execution_authority for row in result.nodes)
    assert any(edge.relation == "ORIGINAL_FULL_PLANNING_CONFIRMATION" for edge in result.edges)


def test_rehashed_full_confirmation_foreign_ref_does_not_resolve() -> None:
    evidence = original(
        "EVIDENCE",
        source_type="FULL_POLICY_CONFIRMATION",
        content={
            "policy_id": str(uuid4()),
            "version_id": str(uuid4()),
            "epoch_id": str(uuid4()),
            "bank_authority": True,
        },
    )
    result = graph([evidence], evidence)
    assert result.state == "UNKNOWN" and result.expected_edge_count == 3
    assert not result.grants_authority
