"""Exact purchase-prefix source lineage, used for comparison only, never cash inputs."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.asset_exposure import _json
from app.domain.bank_posting_codec import POSTING_V2_FIELDS, bank_posting_data
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_owned_asset_read import (
    AcceptedAssetReadProof,
    manifest_rows,
    row_digest,
    validate_read_proof,
)

ACCOUNT_FIELDS = "id user_id account_type balance_cents observed_at".split()
POSITION_FIELDS = (
    "id user_id account_id product_id goal_id policy_version_id principal_cents "
    "purchased_at maturity_at available_at status"
).split()
EVIDENCE_FIELDS = (
    "id user_id source_type source_ref evidence_level content_hash status "
    "observed_at valid_from valid_to"
).split()
BANK_SOURCES = {
    "SIMULATED_BANK_BALANCE",
    "SIMULATED_BANK_POSITION",
    "SIMULATED_BANK_TRANSACTION",
    "SIMULATED_GOAL_OWNERSHIP",
    "SIMULATED_GOAL_MONTH_CONTRIBUTION",
    "SIMULATED_PRINCIPAL_AVAILABILITY",
}
REPLACED_SOURCES = {
    "SIMULATED_BANK_BALANCE",
    "SIMULATED_GOAL_OWNERSHIP",
    "SIMULATED_GOAL_MONTH_CONTRIBUTION",
    "SIMULATED_NEW_FUNDS_LEDGER",
}
BANK_FIELDS = (
    "id user_id action_plan_id legacy_redemption_id closing_position_id operation_type "
    "business_key idempotency_key request request_hash requested_at available_at settled_at "
    "status created_at"
).split()
RECEIPT_FIELDS = (
    "id user_id action_plan_id attempt_number receipt_ref status executed_cents "
    "fee_cents loss_cents response occurred_at reconciled_at created_at"
).split()
POSTING_FIELDS = (
    "id user_id ledger_key account_id position_id redemption_id previous_posting_id "
    "sequence_number entry_kind balance_before_cents delta_cents balance_after_cents "
    "occurred_at created_at ledger_dimension ledger_metadata operation_id leg_ref"
).split()


def instant(raw: str) -> datetime:
    result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Native lineage requires aware exact clocks")
    return result.astimezone(UTC)


def rows_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {row["id"]: row for row in rows}
    if len(result) != len(rows) or any(type(key) is not str for key in result):
        raise ValueError("Complete lineage originals need unique identities")
    return result


def source_transition_map(
    proof: AcceptedAssetReadProof,
) -> tuple[dict[str, str], set[str]]:
    """Verify every generated proof and supersedes step of this exact receipt prefix."""
    validate_read_proof(proof)
    originals = rows_by_id(proof.evidence_originals)
    roots: dict[str, str] = {}
    generated: set[str] = set()
    if len(proof.prefix_originals) != proof.settled_prefix:
        raise ValueError("Exact independently settled receipt prefix originals required")
    for number, (effect, prefix) in enumerate(
        zip(proof.effects, proof.prefix_originals, strict=False), 1
    ):
        bank, receipt, postings = prefix["bank"], prefix["receipt"], prefix["postings"]
        action = proof.slots[number - 1].action_original
        if action is None:
            raise ValueError("Settled physical prefix action is required")
        if (
            bank["id"] != str(effect.operation_id)
            or bank["status"] != "SETTLED"
            or bank["request"] != action["request"]["execution"]
            or bank["request_hash"] != configuration_hash(bank["request"])
            or receipt["id"] != str(uuid5(effect.operation_id, "receipt"))
            or receipt["status"] != "SUCCEEDED"
            or receipt["executed_cents"] != effect.amount_cents
            or receipt["fee_cents"] != 0
            or receipt["loss_cents"] != 0
            or receipt["response"]["bank_operation_id"] != str(effect.operation_id)
            or sorted(receipt["response"]["posting_ids"]) != sorted(row["id"] for row in postings)
            or not postings
            or any(type(row["delta_cents"]) is not int for row in postings)
            or sum(row["delta_cents"] for row in postings) != 0
            or any(
                row["operation_id"] != bank["id"] or row["user_id"] != str(proof.user_id)
                for row in postings
            )
            or len({row["leg_ref"] for row in postings}) != len(postings)
            or instant(bank["settled_at"]) > proof.as_of
        ):
            raise ValueError("Exact native purchase receipt/bank/posting prefix required")
        cash = {row["account_id"]: row for row in postings if row["leg_ref"].startswith("cash:")}
        if len(cash) != len(effect.cash_uses) or any(
            cash.get(str(use.account_id), {}).get("delta_cents") != -use.amount_cents
            for use in effect.cash_uses
        ):
            raise ValueError("Prefix cash legs cannot explain another operation")
        when = receipt["reconciled_at"]
        selected = [
            row
            for row in originals.values()
            if row.get("supersedes_id") is not None
            and row["id"]
            == str(
                uuid5(effect.operation_id, f"evidence:{row['supersedes_id']}:{row['content_hash']}")
            )
        ]
        if len({row["supersedes_id"] for row in selected}) != len(selected):
            raise ValueError("One exact native successor per predecessor and purchase is required")
        for row in selected:
            previous = originals.get(row["supersedes_id"])
            if previous is None:
                raise ValueError("Complete predecessor proof must remain readable")
            source = row["source_type"]
            old, new = deepcopy(previous["content"]), deepcopy(row["content"])
            if (
                source not in REPLACED_SOURCES
                or previous["source_type"] != source
                or row["user_id"] != str(proof.user_id)
                or previous["user_id"] != str(proof.user_id)
                or row["content_hash"] != configuration_hash(new)
                or previous["content_hash"] != configuration_hash(old)
                or previous["status"] != "SUPERSEDED"
                or instant(row["observed_at"]) != instant(when)
                or instant(row["valid_from"]) != instant(when)
                or row["evidence_level"] != previous["evidence_level"]
            ):
                raise ValueError("Only exact native purchase proof replacements are descendants")
            expected = deepcopy(old)
            expected["as_of"] = new["as_of"]
            if instant(new["as_of"]) != instant(when):
                raise ValueError("Projection lineage clock differs from actual receipt")
            if source == "SIMULATED_BANK_BALANCE":
                leg = cash.get(old.get("account_id"))
                if leg is None or old["balance_cents"] != leg["balance_before_cents"]:
                    raise ValueError("Original cash predecessor does not match native posting")
                expected["balance_cents"] = leg["balance_after_cents"]
                expected["bank_posting_id"] = leg["id"]
            elif source == "SIMULATED_GOAL_OWNERSHIP" and old["goal_id"] == str(effect.goal_id):
                if old["cash_owned_cents"] < effect.amount_cents:
                    raise ValueError("Owned prefix cannot invent pre-purchase goal cash")
                expected["cash_owned_cents"] -= effect.amount_cents
                expected["principal_owned_cents"] += effect.amount_cents
                expected["position_ids"] = sorted(old["position_ids"] + [str(effect.position_id)])
            if new != expected:
                raise ValueError("External proof changes cannot be treated as self descendants")
            roots[row["id"]] = roots.get(previous["id"], previous["id"])
        expected_ids = {
            str(uuid5(effect.operation_id, "position-evidence")),
            *(
                [str(uuid5(effect.operation_id, "availability"))]
                if effect.purchase_exit and effect.purchase_exit.kind == "FIXED_MATURITY"
                else []
            ),
            *(
                str(uuid5(UUID(identity), "evidence"))
                for identity in receipt["response"]["transaction_ids"]
            ),
        }
        for identity in expected_ids:
            created = originals.get(identity)
            if (
                created is None
                or created["content_hash"] != configuration_hash(created["content"])
                or created["user_id"] != str(proof.user_id)
                or created["evidence_level"] != "BANK_CONFIRMED"
                or created["source_type"]
                not in {
                    "SIMULATED_BANK_POSITION",
                    "SIMULATED_PRINCIPAL_AVAILABILITY",
                    "SIMULATED_BANK_TRANSACTION",
                }
                or created["supersedes_id"] is not None
                or created["content"].get("bank_operation_id") != str(effect.operation_id)
            ):
                raise ValueError("Each newly materialized prefix proof needs its native operation")
        generated.update(expected_ids)
    return roots, generated


def verify_physical_descendants(
    proof: AcceptedAssetReadProof, before: dict[str, Any]
) -> tuple[dict[str, str], set[str]]:
    """Source comparison may invert exact prefix deltas; current readers do not."""
    roots, generated = source_transition_map(proof)
    current = proof.exposure_original
    old = deepcopy(before)
    if set(old) != set(current):
        raise ValueError("Native exposure source protocol cannot change")
    own_ids = {str(effect.operation_id) for effect in proof.effects}
    account_rows = rows_by_id(proof.account_originals)
    position_rows = rows_by_id(proof.position_originals)
    evidence_rows = rows_by_id(proof.evidence_originals)
    if {
        identity: row_digest(row, ACCOUNT_FIELDS) for identity, row in account_rows.items()
    } != manifest_rows(current, "accounts"):
        raise ValueError("Complete current account originals must match the native manifest")
    if {
        identity: row_digest(row, POSITION_FIELDS) for identity, row in position_rows.items()
    } != manifest_rows(current, "positions"):
        raise ValueError("Complete current positions must match the native manifest")
    relevant = {
        key: row for key, row in evidence_rows.items() if row["source_type"] in BANK_SOURCES
    }
    if {key: row_digest(row, EVIDENCE_FIELDS) for key, row in relevant.items()} != manifest_rows(
        current, "evidence"
    ):
        raise ValueError("All current bank evidence, including predecessor rows, must be retained")
    reconstructed = deepcopy(current)
    current_banks = manifest_rows(current, "bank_operations")
    current_receipts = manifest_rows(current, "receipts")
    current_postings = manifest_rows(current, "bank_postings")
    for prefix in proof.prefix_originals:
        bank, receipt = prefix["bank"], prefix["receipt"]
        if current_banks.get(bank["id"]) != row_digest(bank, BANK_FIELDS) or current_receipts.get(
            receipt["id"]
        ) != row_digest(receipt, RECEIPT_FIELDS):
            raise ValueError(
                "Receipt/bank originals must match the complete current source manifest"
            )
        for post in prefix["postings"]:
            digest = (
                configuration_hash(
                    _json(bank_posting_data({key: post[key] for key in POSTING_V2_FIELDS}))
                )
                if current["protocol"] == "asset-exposure-v4"
                else row_digest(post, POSTING_FIELDS)
            )
            if current_postings.get(post["id"]) != digest:
                raise ValueError("Every prefix bank posting retains its independent native hash")
    prefix_ids = {str(effect.operation_id) for effect in proof.effects[: proof.settled_prefix]}
    position_ids = {str(effect.position_id) for effect in proof.effects[: proof.settled_prefix]}
    receipt_ids = {row["receipt"]["id"] for row in proof.prefix_originals}
    posting_ids = {post["id"] for row in proof.prefix_originals for post in row["postings"]}
    removed = {
        "actions": own_ids,
        "resource_reservations": {
            row["id"] for slot in proof.slots for row in slot.resource_originals
        },
        "positions": position_ids,
        "receipts": receipt_ids,
        "bank_operations": prefix_ids,
        "bank_postings": posting_ids,
        "evidence": set(roots) | generated,
    }
    for table, identities in removed.items():
        reconstructed[table] = [row for row in reconstructed[table] if row["id"] not in identities]
    reconstructed["settlements"] = [
        row for row in reconstructed["settlements"] if row["action_id"] not in own_ids
    ]
    # Verify each new position is the retained real position of its exact receipt.
    for effect, prefix in zip(proof.effects, proof.prefix_originals, strict=False):
        row = position_rows.get(str(effect.position_id))
        if (
            row is None
            or any(
                row[key] != value
                for key, value in {
                    "user_id": str(proof.user_id),
                    "account_id": str(effect.position_account_id),
                    "product_id": str(effect.product_id),
                    "goal_id": str(effect.goal_id),
                    "policy_version_id": str(effect.policy_version_id),
                    "principal_cents": effect.amount_cents,
                    "status": "HELD",
                }.items()
            )
            or instant(row["purchased_at"]) != instant(prefix["bank"]["settled_at"])
        ):
            raise ValueError("Actual settled prefix position cannot disappear or change ownership")
    old_accounts = manifest_rows(old, "accounts")
    for identity, account in account_rows.items():
        legs = [
            post
            for prefix in proof.prefix_originals
            for post in prefix["postings"]
            if post["leg_ref"] == "cash:" + identity
        ]
        if not legs:
            continue
        if account["balance_cents"] != legs[-1]["balance_after_cents"] or any(
            a["balance_after_cents"] != b["balance_before_cents"]
            for a, b in zip(legs, legs[1:], strict=False)
        ):
            raise ValueError("Exact original prefix cash chain is incomplete")
        previous = [
            row
            for key, row in evidence_rows.items()
            if row["source_type"] == "SIMULATED_BANK_BALANCE"
            and row["content"].get("account_id") == identity
            and key not in roots
            and row_digest(
                {
                    **account,
                    "balance_cents": legs[0]["balance_before_cents"],
                    "observed_at": _json(instant(row["content"]["as_of"])),
                },
                ACCOUNT_FIELDS,
            )
            == old_accounts.get(identity)
        ]
        if len(previous) != 1:
            raise ValueError("Independent exact original account watermark is missing")
        restored = {
            **account,
            "balance_cents": legs[0]["balance_before_cents"],
            "observed_at": _json(instant(previous[0]["content"]["as_of"])),
        }
        reconstructed["accounts"] = [
            {"id": identity, "digest": row_digest(restored, ACCOUNT_FIELDS)}
            if item["id"] == identity
            else item
            for item in reconstructed["accounts"]
        ]
    for item in reconstructed["evidence"]:
        row = relevant[item["id"]]
        if item["digest"] == manifest_rows(old, "evidence").get(item["id"]):
            continue
        if row["id"] not in set(roots.values()):
            raise ValueError("Unrelated evidence state changed")
        item["digest"] = row_digest({**row, "status": "VALID"}, EVIDENCE_FIELDS)
    reconstructed.pop("as_of")
    old.pop("as_of")
    if reconstructed != old:
        raise ValueError("Unrelated financial rows, bank sources or pending changed")
    return roots, generated


def prefix_source_equivalent(
    proof: AcceptedAssetReadProof, before: dict[str, Any], current: dict[str, Any]
) -> bool:
    """Invert only proven self descendants in a comparison copy, not a live context."""
    try:
        left, right = deepcopy(before), deepcopy(current)
        fields = {
            "protocol",
            "user_id",
            "epoch_id",
            "shanghai_day",
            "native_source_fingerprint",
            "original_sources",
            "original_policies",
            "full_policy",
            "catalogue",
            "full_protection_sources",
            "current_world_signatures",
            "current_world_status",
            "complete_native_basis",
        }
        if "source_fingerprint_version" in left or "source_fingerprint_version" in right:
            fields.add("source_fingerprint_version")
            if any(manifest.get("source_fingerprint_version") != "full-asset-mode-source-clock-v2"
                   for manifest in (left, right)):
                return False
        if set(left) != fields or set(right) != fields:
            return False
        for manifest in (left, right):
            if (
                manifest["protocol"] != "zhiyu-full-asset-mode-question-v1"
                or manifest["user_id"] != str(proof.user_id)
                or manifest["epoch_id"] != str(proof.epoch_id)
                or manifest["shanghai_day"]
                != proof.as_of.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
                or manifest["current_world_status"] != ["KNOWN", "KNOWN"]
                or len(manifest["current_world_signatures"]) != 2
                or manifest["current_world_signatures"][0] is None
                or len(set(manifest["current_world_signatures"])) != 1
            ):
                return False
        old_exp = [
            item
            for item in left["original_sources"]
            if item["source_type"] == "SIMULATED_ASSET_EXPOSURE"
        ]
        new_exp = [
            item
            for item in right["original_sources"]
            if item["source_type"] == "SIMULATED_ASSET_EXPOSURE"
        ]
        if (
            len(old_exp) != 1
            or len(new_exp) != 1
            or new_exp[0]["id"] != str(proof.exposure_evidence_id)
            or new_exp[0]["content_hash"] != proof.exposure_evidence_hash
            or new_exp[0]["content"] != proof.exposure_original
        ):
            return False
        roots, generated = verify_physical_descendants(proof, old_exp[0]["content"])
        old_sources = rows_by_id(left["original_sources"])
        new_sources = rows_by_id(right["original_sources"])
        if any(
            row["content_hash"] != configuration_hash(row["content"])
            or row["content_hash"] != row["captured_content_hash"]
            or row["content_integrity"] != "VERIFIED"
            or row["status_at_decision"] != "VALID"
            for row in (*old_sources.values(), *new_sources.values())
        ):
            return False
        mapping = roots | {new_exp[0]["id"]: old_exp[0]["id"]}
        normalized_sources = []
        for identity, row in new_sources.items():
            if (
                row["content_hash"] != configuration_hash(row["content"])
                or row["content_integrity"] != "VERIFIED"
            ):
                return False
            if identity in generated:
                continue
            root = mapping.get(identity, identity)
            original = old_sources.get(root)
            if original is None:
                return False
            if identity != root or identity in roots.values():
                if identity == root and row["content"] != original["content"]:
                    return False
                normalized_sources.append(original)
            else:
                normalized_sources.append(row)
        if len({row["id"] for row in normalized_sources}) != len(normalized_sources):
            return False
        right["original_sources"] = sorted(normalized_sources, key=lambda row: row["id"])
        left["original_sources"].sort(key=lambda row: row["id"])
        old_basis, basis = left["complete_native_basis"], right["complete_native_basis"]
        basis_fields = {
            "user_id",
            "as_of",
            "snapshot",
            "versions",
            "positions",
            "products",
            "exposure",
            "income",
            "evidence",
        }
        if (
            set(old_basis) != basis_fields
            or set(basis) != basis_fields
            or old_basis["user_id"] != str(proof.user_id)
            or basis["user_id"] != str(proof.user_id)
            or instant(basis["as_of"]) != proof.as_of
        ):
            return False

        def references(value: Any) -> Any:
            # Only typed evidence reference fields are rewritten, never amounts,
            # account/position/current-ownership/product/policy references.
            if type(value) is dict:
                return {
                    key: (
                        [
                            mapping.get(identity, identity)
                            for identity in item
                            if identity not in generated
                        ]
                        if key == "evidence_ids"
                        else references(item)
                    )
                    for key, item in value.items()
                }
            if type(value) is list:
                return [references(item) for item in value]
            return value

        basis = references(basis)
        right["complete_native_basis"] = basis
        effect_ids = {str(effect.position_id) for effect in proof.effects[: proof.settled_prefix]}
        basis["positions"] = [
            row for row in basis["positions"] if row["position_id"] not in effect_ids
        ]
        accounts = {row["account_id"]: row for row in basis["snapshot"]["cash_accounts"]}
        goals = {row["goal_id"]: row for row in basis["snapshot"]["goals"]}
        for effect in proof.effects[: proof.settled_prefix]:
            for use in effect.cash_uses:
                row = accounts[str(use.account_id)]
                row["balance_cents"] += use.amount_cents
                row["observed_at"] = next(
                    item["observed_at"]
                    for item in old_basis["snapshot"]["cash_accounts"]
                    if item["account_id"] == str(use.account_id)
                )
            row = goals[str(effect.goal_id)]
            row["cash_owned_cents"] += effect.amount_cents
            row["principal_owned_cents"] -= effect.amount_cents
        if basis["exposure"] is None:
            return False
        # _basis uses general exposure: goal-owned purchases cannot increase
        # its general-managed amount. Preserve all foreign pending and cash.
        basis["evidence"] = sorted(
            [
                {
                    "id": mapping.get(row["id"], row["id"]),
                    "hash": old_sources[mapping.get(row["id"], row["id"])]["content_hash"],
                }
                for row in basis["evidence"]
                if row["id"] not in generated
            ],
            key=lambda row: row["id"],
        )
        for item in (old_basis, basis):
            item.pop("as_of")
            item["snapshot"].pop("as_of")
            for reserve in item["snapshot"].get("living_reserves", []):
                reserve.pop("estimation_input_digest", None)
            for name in ("income", "exposure"):
                if isinstance(item[name], dict):
                    item[name].pop("as_of", None)
        basis["snapshot"]["source_digest"] = old_basis["snapshot"]["source_digest"]
        right["native_source_fingerprint"] = left["native_source_fingerprint"]
        for item in (left, right):
            item.pop("current_world_signatures")
            item.pop("current_world_status")
        return left == right
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        return False
