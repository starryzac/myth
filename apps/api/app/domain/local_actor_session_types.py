"""A server authenticated local actor is distinct from a claimed or observed human."""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from pydantic import model_validator

ActorRole = Literal["USER", "AGENT", "REVIEWER", "SYSTEM", "DEMO_ADMIN"]


class LocalActorPrincipal(BoundaryModel):
    user_id: UUID
    role: ActorRole
    session_id: UUID
    issued_at: datetime
    expires_at: datetime
    authentication_source: Literal["LOCAL_SIGNED_SESSION"] = "LOCAL_SIGNED_SESSION"
    authenticated: Literal[True] = True
    human_identity_verified: Literal[False] = False

    @model_validator(mode="after")
    def finite_session(self) -> Self:
        for clock in (self.issued_at, self.expires_at):
            if clock.tzinfo is None or clock.utcoffset() is None:
                raise ValueError("Local actor clocks must be aware")
        if not 0 < (self.expires_at - self.issued_at).total_seconds() <= 900:
            raise ValueError("A local actor session lasts at most fifteen minutes")
        return self


def require_local_user(principal: LocalActorPrincipal, user_id: UUID, now: datetime) -> None:
    """Only trusted USER authentication can originate a new payee permission."""
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or principal.user_id != user_id
        or principal.role != "USER"
        or not principal.issued_at <= now.astimezone(UTC) < principal.expires_at
    ):
        raise ValueError("Current authenticated USER session required")
