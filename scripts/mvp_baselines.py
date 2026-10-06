"""Distinct MVP baseline candidate rules; MODEL_ONLY, no bank execution or authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Arm = Literal["B0", "B1", "B2", "B3", "P"]
Level = Literal["AUTO_EXECUTE", "ASK_ONCE", "ADVISE_ONLY", "BLOCKED"]
Disposition = Literal[
    "NO_ACTION",
    "MANUAL_CHOICE",
    "PROPOSE_AUTONOMOUS",
    "ASK_EVERY_ACTION",
    "ASK_ONCE",
    "ADVISE_ONLY",
    "BLOCKED",
]
RULE_VERSION = "mvp-baseline-candidates-draft-v1"


def nonnegative(value: int, name: str) -> None:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError(f"{name} must be a bounded nonnegative integer number of cents")


@dataclass(frozen=True)
class ProductionProposal:
    """The caller must bind this to a real service result in the experiment manifest.

    This DTO does not validate money or authority. Passing a test fixture here does
    not become a real P decision, authorization, receipt or financial evidence.
    """

    amount_cents: int
    autonomy_level: Level
    decision_run_id: str
    effect_hash: str
    raw_result_sha256: str

    def __post_init__(self) -> None:
        nonnegative(self.amount_cents, "production amount")
        if self.autonomy_level not in {"AUTO_EXECUTE", "ASK_ONCE", "ADVISE_ONLY", "BLOCKED"}:
            raise ValueError("Unknown production autonomy level")
        if not self.decision_run_id:
            raise ValueError("A production proposal requires its original decision identity")
        for digest in (self.effect_hash, self.raw_result_sha256):
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("A production proposal requires original lowercase SHA256 refs")


@dataclass(frozen=True)
class BaselineInput:
    """Same observed current cash, registered rule inputs, and optional real P proposal.

    B0's fixed amount and B1's threshold are external predeclared design inputs.
    B2's fixed-obligation sum must be independently sourced for the declared horizon.
    Dynamic reserve/goal/liquidity information is deliberately absent from B1/B2.
    No forecast income or uncredited principal is represented as current cash.
    """

    observed_cash_cents: int
    manual_fixed_cents: int
    balance_threshold_cents: int
    fixed_obligations_cents: int
    fixed_horizon_days: int = 91
    production: ProductionProposal | None = None

    def __post_init__(self) -> None:
        for field in (
            "observed_cash_cents",
            "manual_fixed_cents",
            "balance_threshold_cents",
            "fixed_obligations_cents",
        ):
            nonnegative(getattr(self, field), field)
        if type(self.fixed_horizon_days) is not int or not 1 <= self.fixed_horizon_days <= 3660:
            raise ValueError("The static fixed-expense horizon must be explicitly registered")


@dataclass(frozen=True)
class CandidateDecision:
    arm_id: Arm
    proposed_amount_cents: int
    disposition: Disposition
    mechanism: str
    production_decision_ref: str | None = None
    production_effect_hash: str | None = None
    production_result_sha256: str | None = None
    rule_version: str = RULE_VERSION
    execution_mode: Literal["MODEL_ONLY"] = "MODEL_ONLY"
    authority_granted: Literal[False] = False
    executed: Literal[False] = False
    actor_kind: Literal["SYNTHETIC_SCRIPTED_ACTOR"] = "SYNTHETIC_SCRIPTED_ACTOR"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def decide(arm: Arm, facts: BaselineInput) -> CandidateDecision:
    """Run a distinct declared mechanism and return a proposal, never an action.

    B3 changes actual confirmation requirements for the same P economic proposal.
    A service adapter must still implement these choices and observe actual results;
    MODEL_ONLY decisions cannot be exported as service-integration effect metrics.
    """
    if arm == "B0":
        amount = facts.manual_fixed_cents
        return CandidateDecision(
            arm,
            amount,
            "MANUAL_CHOICE" if amount else "NO_ACTION",
            "Scripted manual actor selects its registered fixed amount; no Agent trigger/recovery",
        )
    if arm == "B1":
        amount = max(0, facts.observed_cash_cents - facts.balance_threshold_cents)
        return CandidateDecision(
            arm,
            amount,
            "PROPOSE_AUTONOMOUS" if amount else "NO_ACTION",
            "Balance threshold only: current cash minus the predeclared fixed threshold",
        )
    if arm == "B2":
        amount = max(0, facts.observed_cash_cents - facts.fixed_obligations_cents)
        return CandidateDecision(
            arm,
            amount,
            "PROPOSE_AUTONOMOUS" if amount else "NO_ACTION",
            "Static fixed-expense rule only: current cash minus registered horizon obligations",
        )
    if arm not in {"B3", "P"}:
        raise ValueError("Unknown MVP baseline arm")
    proposal = facts.production
    if proposal is None:
        raise ValueError("B3/P require an original real-service proposal; no fabricated fallback")
    amount = proposal.amount_cents
    disposition: Disposition
    if proposal.autonomy_level == "BLOCKED":
        disposition = "BLOCKED"
    elif proposal.autonomy_level == "ADVISE_ONLY":
        disposition = "ADVISE_ONLY"
    elif not amount:
        disposition = "NO_ACTION"
    elif arm == "B3":
        disposition = "ASK_EVERY_ACTION"
    elif proposal.autonomy_level == "ASK_ONCE":
        disposition = "ASK_ONCE"
    else:
        disposition = "PROPOSE_AUTONOMOUS"
    return CandidateDecision(
        arm,
        amount,
        disposition,
        "Every executable action needs exact synthetic actor confirmation"
        if arm == "B3"
        else "Preserve the original actual service proposal and autonomy result",
        proposal.decision_run_id,
        proposal.effect_hash,
        proposal.raw_result_sha256,
    )
