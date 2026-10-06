"""Register explicit classification events without modifying retained audit originals."""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_transaction_category_audit"
down_revision: str | Sequence[str] | None = "0010_product_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGISTER = r"""
DO $$
DECLARE original TEXT;
BEGIN
 SELECT pg_get_functiondef('public.audit_event_insert()'::regprocedure) INTO original;
 IF position('''GOAL_INITIALIZED''))' IN original)=0
    OR position('TRANSACTION_CATEGORY_CONFIRMED' IN original)>0
 THEN RAISE EXCEPTION 'Expected original 0007 audit protocol guard'; END IF;
 EXECUTE replace(original, '''GOAL_INITIALIZED''))',
  '''GOAL_INITIALIZED'',''TRANSACTION_CATEGORY_CONFIRMED''))');
END $$;
"""

GUARD = r"""
CREATE FUNCTION guard_transaction_category_audit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE before_data JSONB; after_data JSONB; declaration JSONB; bank JSONB; command JSONB;
BEGIN
 IF NEW.event_type<>'TRANSACTION_CATEGORY_CONFIRMED' THEN RETURN NEW; END IF;
 IF NEW.payload_version<>1 OR NEW.aggregate_type<>'TRANSACTION'
 OR NEW.aggregate_id<>NEW.correlation_id OR NEW.payload->>'correlation_kind'<>'TRANSACTION'
 OR NEW.decision_run_id IS NOT NULL OR NEW.action_plan_id IS NOT NULL
 OR NEW.action_receipt_id IS NOT NULL
 OR NEW.payload->'context'->>'reason_code'<>'USER_CLASSIFICATION_CONFIRMED'
 OR jsonb_array_length(NEW.payload->'references')<>4
 THEN RAISE EXCEPTION 'CATEGORY_AUDIT_IDENTITY'; END IF;
 SELECT s.canonical_text::jsonb->'data' INTO STRICT before_data FROM jsonb_array_elements(NEW.payload->'references') r
 JOIN public.audit_subject_snapshots s ON s.user_id=NEW.user_id AND s.epoch_id=NEW.epoch_id
 AND s.kind=r->>'kind' AND s.entity_id=(r->>'id')::uuid AND s.snapshot_hash=r->>'snapshot_hash'
 WHERE r->>'kind'='TRANSACTION' AND (r->>'id')::uuid=NEW.aggregate_id AND r->>'role'='BEFORE';
 SELECT s.canonical_text::jsonb->'data' INTO STRICT after_data FROM jsonb_array_elements(NEW.payload->'references') r
 JOIN public.audit_subject_snapshots s ON s.user_id=NEW.user_id AND s.epoch_id=NEW.epoch_id
 AND s.kind=r->>'kind' AND s.entity_id=(r->>'id')::uuid AND s.snapshot_hash=r->>'snapshot_hash'
 WHERE r->>'kind'='TRANSACTION' AND (r->>'id')::uuid=NEW.aggregate_id AND r->>'role'='AFTER';
 SELECT s.canonical_text::jsonb->'data' INTO STRICT bank FROM jsonb_array_elements(NEW.payload->'references') r
 JOIN public.audit_subject_snapshots s ON s.user_id=NEW.user_id AND s.epoch_id=NEW.epoch_id
 AND s.kind=r->>'kind' AND s.entity_id=(r->>'id')::uuid AND s.snapshot_hash=r->>'snapshot_hash'
 WHERE r->>'kind'='EVIDENCE' AND (r->>'id')::uuid=(before_data->>'evidence_id')::uuid
 AND r->>'role'='BASIS';
 SELECT s.canonical_text::jsonb->'data' INTO STRICT declaration FROM jsonb_array_elements(NEW.payload->'references') r
 JOIN public.audit_subject_snapshots s ON s.user_id=NEW.user_id AND s.epoch_id=NEW.epoch_id
 AND s.kind=r->>'kind' AND s.entity_id=(r->>'id')::uuid AND s.snapshot_hash=r->>'snapshot_hash'
 WHERE r->>'kind'='EVIDENCE' AND r->>'role'='AFTER';
 command := declaration->'content'->'original_command';
 IF before_data->'category_confirmed' IS DISTINCT FROM 'false'::jsonb
 OR after_data->'category_confirmed' IS DISTINCT FROM 'true'::jsonb
 OR (before_data-ARRAY['category','category_confirmed']) IS DISTINCT FROM
    (after_data-ARRAY['category','category_confirmed'])
 OR before_data->>'direction'<>'DEBIT'
 OR bank->>'evidence_level'<>'BANK_CONFIRMED'
 OR bank->>'source_type'<>'SIMULATED_BANK_TRANSACTION'
 OR bank->'content'->>'economic_role'<>'CONSUMPTION'
 OR declaration->>'evidence_level'<>'USER_DECLARED'
 OR declaration->>'source_type'<>'SIMULATED_USER_CATEGORY_CONFIRMATION'
 OR declaration->'content'->'confirmed' IS DISTINCT FROM 'true'::jsonb
 OR declaration->'content'->>'actor'<>'synthetic_user'
 OR command->>'protocol'<>'transaction-category-command-v1'
 OR (command->>'user_id')::uuid<>NEW.user_id
 OR (command->>'transaction_id')::uuid<>NEW.aggregate_id
 OR command->'request'->'accepted' IS DISTINCT FROM 'true'::jsonb
 OR (command->'request'->>'expected_epoch_id')::uuid<>NEW.epoch_id
 OR command->'request'->>'category' NOT IN
   ('food','transport','daily_necessities','rent','utilities','education','healthcare','other')
 OR after_data->>'category' IS DISTINCT FROM command->'request'->>'category'
 OR declaration->'content'->>'category' IS DISTINCT FROM after_data->>'category'
 OR NEW.payload->'context'->>'cause_ref' IS DISTINCT FROM declaration->>'id'
 OR NEW.payload->'context'->'details'->>'command_hash' IS DISTINCT FROM
    declaration->'content'->>'command_hash'
 THEN RAISE EXCEPTION 'CATEGORY_AUDIT_ORIGINALS'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER transaction_category_audit_insert BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION guard_transaction_category_audit();
"""


def upgrade() -> None:
    op.execute(REGISTER)
    op.execute(GUARD)


def downgrade() -> None:
    op.execute(r"""
DO $$
DECLARE original TEXT;
BEGIN
 IF EXISTS(SELECT 1 FROM audit_events WHERE event_type='TRANSACTION_CATEGORY_CONFIRMED')
 THEN RAISE EXCEPTION 'Refusing to discard retained category audit protocol'; END IF;
 SELECT pg_get_functiondef('public.audit_event_insert()'::regprocedure) INTO original;
 IF position('''GOAL_INITIALIZED'',''TRANSACTION_CATEGORY_CONFIRMED''))' IN original)=0
 THEN RAISE EXCEPTION 'Expected registered category audit guard'; END IF;
 EXECUTE replace(original, '''GOAL_INITIALIZED'',''TRANSACTION_CATEGORY_CONFIRMED''))',
  '''GOAL_INITIALIZED''))');
END $$;
DROP TRIGGER transaction_category_audit_insert ON audit_events;
DROP FUNCTION guard_transaction_category_audit();
""")
