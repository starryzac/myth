"""Request-local reuse of a successful immutable v3 plan proof, never current facts.

Every call hashes the complete original content again. Current
ownership, scope, policies, money, bank rows, audit anchors and clock checks are
outside this helper and must always run. Legacy v2 keeps its existing behavior.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.domain.policy_configuration import configuration_hash

V3 = "registered-joint-goal-execution-archive-v3"
V4 = "registered-joint-goal-execution-source-dag-v4"
MAX_PROOFS = 16
_proofs: ContextVar[set[str] | None] = ContextVar("joint_immutable_request_proofs", default=None)


@contextmanager
def immutable_joint_validation_scope() -> Iterator[None]:
    """One fresh bounded scope per request; nested native calls share only this scope."""
    current = _proofs.get()
    if current is not None:
        yield
        return
    token = _proofs.set(set())
    try:
        yield
    finally:
        _proofs.reset(token)


def verify_original_content(
    protocol: str,
    original_without_plan_hash: Mapping[str, Any],
    claimed_plan_hash: str,
    uncached_complete_verifier: Callable[[], None],
) -> None:
    computed = configuration_hash(dict(original_without_plan_hash))
    if computed != claimed_plan_hash:
        raise ValueError("Complete original immutable plan hash differs")
    current = _proofs.get()
    if protocol not in {V3, V4} or current is None:
        uncached_complete_verifier()
        return
    identity = configuration_hash({"protocol": protocol, "full_content_hash": computed})
    if identity in current:
        return
    # A failed or interrupted verifier cannot leave a positive proof.
    uncached_complete_verifier()
    if len(current) < MAX_PROOFS:
        current.add(identity)
