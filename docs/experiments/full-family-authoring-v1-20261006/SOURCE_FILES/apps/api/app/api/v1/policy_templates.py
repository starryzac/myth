"""Read-only finite policy schemas and candidate validation; no policy is persisted."""

from typing import Annotated, Any, Literal

from app.api.errors import ErrorEnvelope
from app.domain.full_policy_configuration import (
    FULL_TYPE_MAPPING,
    MVP_TYPE_MAPPING,
    DSLVersion,
    TemplateName,
    template_names,
    template_schema,
    validate_full_configuration,
)
from app.domain.policy_configuration import StrictModel, configuration_hash
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(
    prefix="/api/v1/policy-templates",
    tags=["有限策略模板"],
    responses={422: {"model": ErrorEnvelope}},
)


class TemplateDescriptor(BaseModel):
    template_name: TemplateName
    full_configuration_type: str
    mvp_configuration_type: str | None
    available_versions: list[DSLVersion]


class TemplateCatalog(BaseModel):
    templates: list[TemplateDescriptor]
    candidate_only: Literal[True] = True
    authority_granted: Literal[False] = False


class TemplateSchemaResponse(BaseModel):
    template_name: TemplateName
    dsl_version: DSLVersion
    json_schema: dict[str, Any]
    schema_sha256: str
    cross_field_validation_required: Literal[True] = True
    candidate_only: Literal[True] = True
    authority_granted: Literal[False] = False


class CandidateValidationRequest(StrictModel):
    template_name: TemplateName
    dsl_version: DSLVersion = "FULL_V1"
    configuration: dict[str, Any]


class ValidatedCandidate(BaseModel):
    template_name: TemplateName
    dsl_version: DSLVersion
    normalized_configuration: dict[str, Any]
    configuration_hash: str
    candidate_only: Literal[True] = True
    authority_granted: Literal[False] = False
    reference_validation_pending: Literal[True] = True


@router.get("", response_model=TemplateCatalog, operation_id="list_policy_templates")
def list_policy_templates() -> TemplateCatalog:
    return TemplateCatalog(
        templates=[
            TemplateDescriptor(
                template_name=name,
                full_configuration_type=FULL_TYPE_MAPPING[name],
                mvp_configuration_type=MVP_TYPE_MAPPING.get(name),
                available_versions=(
                    ["MVP_V1", "FULL_V1"] if name in MVP_TYPE_MAPPING else ["FULL_V1"]
                ),
            )
            for name in template_names()
        ]
    )


@router.get(
    "/{template_name}/schema",
    response_model=TemplateSchemaResponse,
    operation_id="get_policy_template_schema",
)
def get_policy_template_schema(
    template_name: TemplateName,
    dsl_version: Annotated[DSLVersion, Query()] = "FULL_V1",
) -> TemplateSchemaResponse:
    try:
        schema = template_schema(template_name, dsl_version)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Template/version is not supported") from error
    return TemplateSchemaResponse(
        template_name=template_name,
        dsl_version=dsl_version,
        json_schema=schema,
        schema_sha256=configuration_hash(schema),
    )


@router.post(
    "/validate",
    response_model=ValidatedCandidate,
    operation_id="validate_policy_template_candidate",
)
def validate_policy_template_candidate(body: CandidateValidationRequest) -> ValidatedCandidate:
    try:
        configuration = validate_full_configuration(
            body.template_name, body.configuration, version=body.dsl_version
        )
    except ValueError as error:
        # Validation errors can contain raw user values: never echo those values.
        raise HTTPException(
            status_code=422, detail="Policy candidate fields are invalid"
        ) from error
    return ValidatedCandidate(
        template_name=body.template_name,
        dsl_version=body.dsl_version,
        normalized_configuration=configuration,
        configuration_hash=configuration_hash(configuration),
    )
