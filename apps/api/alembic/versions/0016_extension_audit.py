"""Register honest extension evidence facts without rewriting retained audit events."""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0016_extension_audit"
down_revision: str | Sequence[str] | None = "0015_joint_goal_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_GUARD = "'GOAL_INITIALIZED','TRANSACTION_CATEGORY_CONFIRMED'))"
REGISTERED_GUARD = (
    "'GOAL_INITIALIZED','TRANSACTION_CATEGORY_CONFIRMED','EXTENSION_RECORD_CREATED'))"
)


def patch_event_protocol(definition: str, *, register: bool) -> str:
    """Change one known v1 whitelist occurrence and retain every other guard byte."""
    before, after = (
        (LEGACY_GUARD, REGISTERED_GUARD) if register else (REGISTERED_GUARD, LEGACY_GUARD)
    )
    if (
        definition.count(before) != 1
        or (register and "EXTENSION_RECORD_CREATED" in definition)
        or (not register and definition.count("EXTENSION_RECORD_CREATED") != 1)
    ):
        raise ValueError("Expected exact original extension audit protocol guard")
    return definition.replace(before, after, 1)


GUARD = r"""
CREATE FUNCTION guard_extension_record_audit() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE ref JSONB; anchor JSONB; captured JSONB; original JSONB; actual JSONB;
 record_kind TEXT; expected_source TEXT;
BEGIN
 IF NEW.event_type<>'EXTENSION_RECORD_CREATED' THEN RETURN NEW; END IF;
 IF NEW.payload_version IS DISTINCT FROM 1
 OR NEW.aggregate_type IS DISTINCT FROM 'EVIDENCE'
 OR NEW.aggregate_id IS DISTINCT FROM NEW.correlation_id
 OR NEW.payload->>'correlation_kind' IS DISTINCT FROM 'EVIDENCE'
 OR NEW.decision_run_id IS NOT NULL OR NEW.action_plan_id IS NOT NULL
 OR NEW.action_receipt_id IS NOT NULL
 OR NEW.payload->'changes' IS DISTINCT FROM '[]'::jsonb
 OR NEW.payload->'missing_evidence_ids' IS DISTINCT FROM '[]'::jsonb
 OR NEW.payload->'observation' IS DISTINCT FROM 'null'::jsonb
 OR NEW.payload->'legacy_origin' IS DISTINCT FROM 'null'::jsonb
 OR NEW.payload->'epoch_transition' IS DISTINCT FROM 'null'::jsonb
 OR jsonb_typeof(NEW.payload->'references') IS DISTINCT FROM 'array'
 OR jsonb_typeof(NEW.payload->'anchors') IS DISTINCT FROM 'array'
 THEN RAISE EXCEPTION 'EXTENSION_AUDIT_IDENTITY'; END IF;
 IF jsonb_array_length(NEW.payload->'references')<>1
 OR jsonb_array_length(NEW.payload->'anchors')<>1
 OR NEW.payload->>'fact_key' IS DISTINCT FROM 'EXTENSION_RECORD_CREATED:'||NEW.aggregate_id::text
 THEN RAISE EXCEPTION 'EXTENSION_AUDIT_REFERENCE_COUNT'; END IF;
 ref:=NEW.payload->'references'->0;
 anchor:=NEW.payload->'anchors'->0;
 IF ref->>'kind' IS DISTINCT FROM 'EVIDENCE'
 OR (ref->>'id')::uuid IS DISTINCT FROM NEW.aggregate_id
 OR ref->>'role' IS DISTINCT FROM 'AFTER'
 OR ref->>'scope' IS DISTINCT FROM 'TENANT'
 OR (ref->>'user_id')::uuid IS DISTINCT FROM NEW.user_id
 OR (ref->>'snapshot_version')::int IS DISTINCT FROM 1
 OR anchor->>'kind' IS DISTINCT FROM 'EVIDENCE_CONTENT'
 OR (anchor->>'reference_id')::uuid IS DISTINCT FROM NEW.aggregate_id
 OR anchor->>'hash_algorithm' IS DISTINCT FROM 'configuration-sha256-v1'
 OR anchor->>'snapshot_hash' IS DISTINCT FROM ref->>'snapshot_hash'
 THEN RAISE EXCEPTION 'EXTENSION_AUDIT_REFERENCE_ANCHOR'; END IF;
 SELECT s.canonical_text::jsonb INTO STRICT captured FROM public.audit_subject_snapshots s
 WHERE s.user_id=NEW.user_id AND s.epoch_id=NEW.epoch_id AND s.kind='EVIDENCE'
 AND s.entity_id=NEW.aggregate_id AND s.scope='TENANT' AND s.snapshot_version=1
 AND s.snapshot_hash=ref->>'snapshot_hash';
 original:=captured->'data';
 SELECT to_jsonb(e) INTO STRICT actual FROM public.evidence_items e
 WHERE e.id=NEW.aggregate_id AND e.user_id=NEW.user_id;
 IF (captured->>'id')::uuid IS DISTINCT FROM NEW.aggregate_id
 OR (captured->>'user_id')::uuid IS DISTINCT FROM NEW.user_id
 OR (captured->>'epoch_id')::uuid IS DISTINCT FROM NEW.epoch_id
 OR captured->>'kind' IS DISTINCT FROM 'EVIDENCE'
 OR captured->>'scope' IS DISTINCT FROM 'TENANT'
 OR (original->>'id')::uuid IS DISTINCT FROM NEW.aggregate_id
 OR (original->>'user_id')::uuid IS DISTINCT FROM NEW.user_id
 OR original->'content' IS DISTINCT FROM actual->'content'
 OR original->>'content_hash' IS DISTINCT FROM actual->>'content_hash'
 OR original->>'evidence_level' IS DISTINCT FROM actual->>'evidence_level'
 OR original->>'source_type' IS DISTINCT FROM actual->>'source_type'
 OR original->>'source_ref' IS DISTINCT FROM actual->>'source_ref'
 OR original->>'status' IS DISTINCT FROM actual->>'status'
 OR (original->>'created_at')::timestamptz IS DISTINCT FROM
    (actual->>'created_at')::timestamptz
 OR (original->>'observed_at')::timestamptz IS DISTINCT FROM
    (actual->>'observed_at')::timestamptz
 OR (original->>'valid_from')::timestamptz IS DISTINCT FROM
    (actual->>'valid_from')::timestamptz
 OR (original->>'valid_to')::timestamptz IS DISTINCT FROM
    (actual->>'valid_to')::timestamptz
 OR original->'supersedes_id' IS DISTINCT FROM actual->'supersedes_id'
 OR anchor->>'digest' IS DISTINCT FROM original->>'content_hash'
 THEN RAISE EXCEPTION 'EXTENSION_AUDIT_SNAPSHOT_ORIGINAL'; END IF;
 record_kind:=substring(actual->>'source_type' FROM 12);
 IF record_kind NOT IN ('AUTH','CONTROL','OPERATION','EVENT','RESULT','BINDING')
 OR actual->>'source_type' IS DISTINCT FROM 'ZHIYU_NEXT_'||record_kind
 OR actual->>'status' IS DISTINCT FROM 'VALID'
 OR actual->>'evidence_level' IS DISTINCT FROM (CASE WHEN record_kind IN ('AUTH','CONTROL')
    THEN 'USER_CONFIRMED_POLICY' ELSE 'USER_DECLARED' END)
 OR jsonb_typeof(actual->'content') IS DISTINCT FROM 'object'
 OR jsonb_typeof(actual->'content'->'key') IS DISTINCT FROM 'string'
 OR actual->'content'->>'protocol' IS DISTINCT FROM 'zhiyu-next-v1'
 OR actual->'content'->'simulation' IS DISTINCT FROM 'true'::jsonb
 OR actual->'content'->>'environment_id' IS DISTINCT FROM current_database()
 OR (actual->'content'->>'user_id')::uuid IS DISTINCT FROM NEW.user_id
 OR (actual->'content'->>'epoch_id')::uuid IS DISTINCT FROM NEW.epoch_id
 OR NEW.occurred_at IS DISTINCT FROM (actual->>'created_at')::timestamptz
 OR NEW.observed_at IS DISTINCT FROM (actual->>'observed_at')::timestamptz
 THEN RAISE EXCEPTION 'EXTENSION_AUDIT_RECORD_ORIGINAL'; END IF;
 expected_source:=current_database()||':'||record_kind||':'||encode(public.digest(
    convert_to(public.audit_canonical(jsonb_build_object('key',actual->'content'->>'key')),
    'UTF8'),'sha256'),'hex');
 IF actual->>'source_ref' IS DISTINCT FROM expected_source
 OR (record_kind IN ('AUTH','CONTROL') AND (
    actual->'content'->'payload'->>'actor' IS DISTINCT FROM 'USER'
    OR actual->'content'->'payload'->'accepted' IS DISTINCT FROM 'true'::jsonb))
 THEN RAISE EXCEPTION 'EXTENSION_AUDIT_SOURCE_OR_CONSENT'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER extension_record_audit_insert BEFORE INSERT ON public.audit_events
FOR EACH ROW EXECUTE FUNCTION guard_extension_record_audit();
"""


def upgrade() -> None:
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef('public.audit_event_insert()'::regprocedure)")
    )
    if not isinstance(definition, str):
        raise ValueError("Original audit protocol function is missing")
    op.execute(patch_event_protocol(definition, register=True))
    op.execute(GUARD)


def downgrade() -> None:
    retained = op.get_bind().scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM public.audit_events "
            "WHERE event_type='EXTENSION_RECORD_CREATED')"
        )
    )
    if retained:
        raise ValueError("Refusing to discard retained extension audit protocol")
    definition = op.get_bind().scalar(
        text("SELECT pg_get_functiondef('public.audit_event_insert()'::regprocedure)")
    )
    if not isinstance(definition, str):
        raise ValueError("Registered audit protocol function is missing")
    op.execute(patch_event_protocol(definition, register=False))
    op.execute("DROP TRIGGER extension_record_audit_insert ON public.audit_events")
    op.execute("DROP FUNCTION public.guard_extension_record_audit()")
