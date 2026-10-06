"""Read fresh FULL veto sources under the caller's existing user write lock."""

from datetime import datetime
from uuid import UUID

from app.db.full_models import FullPolicy
from app.domain.execution_types import ExecutionContext, ExecutionEffect, ExecutionValidation
from app.domain.full_execution_protection import (
    FullExecutionProtectionResult,
    validate_full_execution_protection,
)
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def has_full_protection_policies(session: Session, user_id: UUID) -> bool:
    """Read the current inventory under the caller lock; never cache authority."""
    if session.scalar(text("SELECT to_regclass('public.full_policies')")) is None:
        return False
    return (
        session.scalar(
            select(FullPolicy.id)
            .where(
                FullPolicy.user_id == user_id,
                FullPolicy.template_name.in_(
                    ["DatedExpensePolicy", "PeriodicTransferPolicy", "SeasonalReservePolicy"]
                ),
            )
            .limit(1)
        )
        is not None
    )


def enforce_full_execution_protection(
    engine: Engine,
    user_id: UUID,
    effect: ExecutionEffect,
    context: ExecutionContext,
    validation: ExecutionValidation,
    now: datetime,
) -> FullExecutionProtectionResult | None:
    """Only a fresh read adds protection; the original grants remain mandatory.

    The write caller must already hold its original per-user lock. FULL lifecycle
    changes take that same lock. This second transaction is read-only and contains
    no receipt, confirmation, financial write, or cross-request cached result.
    Legacy databases without FULL tables retain their original action behavior.
    """
    if (context.user_id, effect.user_id, context.snapshot.as_of) != (user_id, user_id, now):
        raise PolicyLifecycleError(
            "FULL_EXECUTION_BINDING_MISMATCH", "完整保护与原用户、动作或当前时点不一致", 409
        )
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as read:
                if read.scalar(text("SELECT to_regclass('public.full_policies')")) is None:
                    return None
                relevant = read.scalar(
                    select(FullPolicy.id)
                    .where(
                        FullPolicy.user_id == user_id,
                        FullPolicy.template_name.in_(
                            [
                                "DatedExpensePolicy",
                                "PeriodicTransferPolicy",
                                "SeasonalReservePolicy",
                            ]
                        ),
                    )
                    .limit(1)
                )
                if relevant is None:
                    return None
                current = compute_full_annual_protection(read, user_id, now)
                issues = tuple(row.code for row in current.source_issues)
                if not current.projection.full_obligations_complete_within_registered_current_scope:
                    issues += ("FULL_CURRENT_PROTECTION_INVENTORY_NOT_PROVEN",)
                account_bounds = None
                if not issues and validation.projected_snapshot is not None:
                    try:
                        projected_input = FullProtectionProjectionInput(
                            snapshot=validation.projected_snapshot,
                            boundary_versions=context.versions,
                            positions=validation.projected_positions,
                            boundary_products=context.boundary_products,
                            policies=current.full_policy_sources,
                            reserved_cash_by_account=context.reserved_cash_by_account,
                        )
                        projected_full = project_full_protection(projected_input)
                        if projected_full.source_account_checks:
                            account_bounds = derive_full_account_debit_bounds(
                                effect, context, validation, projected_input, projected_full
                            )
                    except (ValueError, TypeError, OverflowError):
                        issues += ("FULL_REGISTERED_ACCOUNT_PROOF_DERIVATION_FAILED",)
                checked = validate_full_execution_protection(
                    effect,
                    context,
                    validation,
                    current.full_policy_sources,
                    source_issues=issues,
                    account_debit_bounds=account_bounds,
                )
    if checked.status in {"BLOCKED", "UNKNOWN"}:
        raise PolicyLifecycleError(
            "FULL_EXECUTION_PROTECTION_BLOCKED"
            if checked.status == "BLOCKED"
            else "FULL_EXECUTION_PROTECTION_UNKNOWN",
            "当前完整版保护未允许原动作：" + ",".join(checked.reasons),
            409,
        )
    return checked
