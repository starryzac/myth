"""Read-only exact action and captured policy-version search, independent of the old list."""

import re

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.domain.full_decision_search import DecisionSearchQuery, DecisionSearchResponse
from app.services.full_decision_search import search_decisions
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

router = APIRouter(prefix="/api/v1/decision-search", tags=["动作与版本审计搜索"])


@router.get("", response_model=DecisionSearchResponse, operation_id="search_decision_originals")
def get_decision_search(
    request: Request,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DecisionSearchResponse:
    allowed = {"action_id", "action_key", "policy_version_id", "epoch_id", "limit", "offset"}
    values: dict[str, str | int] = {}
    for key in request.query_params:
        if key not in allowed or len(request.query_params.getlist(key)) != 1:
            raise HTTPException(422, "只接受单次原动作/策略版本/轮次身份及分页")
        value = request.query_params[key]
        if key in {"limit", "offset"}:
            if not re.fullmatch(r"0|[1-9][0-9]{0,5}", value):
                raise HTTPException(422, "分页必须为整数")
            values[key] = int(value)
        else:
            values[key] = value
    try:
        query = DecisionSearchQuery.model_validate(values)
    except ValidationError as error:
        raise HTTPException(422, "搜索身份或分页合同无效") from error
    return search_decisions(session, user.id, query, now)
