"""Permanent simulated events, protected heads, and epoch-owned original snapshots."""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_audit_chain"
down_revision: str | Sequence[str] | None = "0005_decision_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = """
CREATE TABLE audit_epochs (
 epoch_number INTEGER NOT NULL, schema_version VARCHAR(48) DEFAULT 'audit-head-v1' NOT NULL,
 canonical_version VARCHAR(48) DEFAULT 'audit-canonical-json-v1' NOT NULL,
 status VARCHAR(16) DEFAULT 'OPEN' NOT NULL, opened_at TIMESTAMPTZ NOT NULL,
 previous_epoch_id UUID, previous_seal_hash VARCHAR(64),
 event_count INTEGER DEFAULT '0' NOT NULL, last_sequence INTEGER DEFAULT '0' NOT NULL,
 last_event_id UUID, last_event_hash VARCHAR(64), genesis_event_id UUID,
 genesis_event_hash VARCHAR(64), sealed_at TIMESTAMPTZ, seal_canonical_text TEXT,
 seal_hash VARCHAR(64), archive_manifest_hash VARCHAR(64),
 archive_record_counts JSONB DEFAULT '{}'::jsonb NOT NULL,
 user_id UUID NOT NULL, id UUID NOT NULL, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
 CONSTRAINT pk_audit_epochs PRIMARY KEY(id),
 CONSTRAINT uq_audit_epochs_number UNIQUE(user_id,epoch_number),
 CONSTRAINT uq_audit_epochs_id UNIQUE(id,user_id),
 CONSTRAINT fk_audit_epochs_previous_epoch_id_audit_epochs FOREIGN KEY(previous_epoch_id,user_id)
   REFERENCES audit_epochs(id,user_id) ON DELETE RESTRICT,
 CONSTRAINT fk_audit_epochs_user_id_users FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE
  RESTRICT,
 CONSTRAINT ck_audit_epochs_head_count CHECK(epoch_number>0 AND event_count>=0 AND
  last_sequence=event_count),
 CONSTRAINT ck_audit_epochs_status CHECK(status IN ('OPEN','SEALED')),
 CONSTRAINT ck_audit_epochs_seal CHECK(
   (status='OPEN' AND seal_hash IS NULL AND seal_canonical_text IS NULL AND sealed_at IS NULL)
   OR (status='SEALED' AND seal_hash ~ '^[0-9a-f]{64}$' AND seal_canonical_text IS NOT NULL AND
  sealed_at IS NOT NULL))
);
CREATE INDEX ix_audit_epochs_user_id ON audit_epochs(user_id);
CREATE UNIQUE INDEX uq_audit_epochs_open ON audit_epochs(user_id) WHERE status='OPEN';
CREATE TABLE audit_subject_snapshots (
 epoch_id UUID NOT NULL, kind VARCHAR(48) NOT NULL, entity_id UUID NOT NULL,
 scope VARCHAR(24) NOT NULL, snapshot_version INTEGER DEFAULT '1' NOT NULL,
 canonical_text TEXT NOT NULL, snapshot_hash VARCHAR(64) NOT NULL, captured_at TIMESTAMPTZ NOT NULL,
 user_id UUID NOT NULL, id UUID NOT NULL, created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
 CONSTRAINT pk_audit_subject_snapshots PRIMARY KEY(id),
 CONSTRAINT uq_audit_subject_snapshots_id UNIQUE(id,user_id),
 CONSTRAINT fk_audit_subject_snapshots_epoch_id_audit_epochs FOREIGN KEY(epoch_id,user_id)
   REFERENCES audit_epochs(id,user_id) ON DELETE RESTRICT,
 CONSTRAINT fk_audit_subject_snapshots_user_id_users FOREIGN KEY(user_id) REFERENCES users(id)
  ON DELETE RESTRICT,
 CONSTRAINT uq_audit_subject_version UNIQUE(user_id,epoch_id,kind,entity_id,snapshot_hash),
 CONSTRAINT ck_audit_subject_snapshots_scope_version CHECK(scope IN ('TENANT','GLOBAL_CATALOG')
  AND snapshot_version>0),
 CONSTRAINT ck_audit_subject_snapshots_hash CHECK(snapshot_hash ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_audit_subject_snapshots_size CHECK(octet_length(canonical_text)<=16777216)
);
CREATE INDEX ix_audit_subject_snapshots_user_id ON audit_subject_snapshots(user_id);
"""

FUNCTIONS = r"""
CREATE FUNCTION audit_canonical(v jsonb, depth integer DEFAULT 0) RETURNS text
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE result text; kind text:=jsonb_typeof(v);
BEGIN
 IF depth>32 THEN RAISE EXCEPTION 'AUDIT_JSON_DEPTH'; END IF;
 IF kind='object' THEN
  SELECT '{'||coalesce(string_agg(to_json(k)::text||':'||public.audit_canonical(x,depth+1),','
  ORDER BY k COLLATE "C"),'')||'}'
    INTO result FROM jsonb_each(v) AS t(k,x);
 ELSIF kind='array' THEN
  SELECT '['||coalesce(string_agg(public.audit_canonical(x,depth+1),',' ORDER BY n),'')||']'
    INTO result FROM jsonb_array_elements(v) WITH ORDINALITY AS t(x,n);
 ELSIF kind='number' THEN
  IF v::text !~ '^-?(0|[1-9][0-9]*)$' OR v::text::numeric NOT BETWEEN -9223372036854775808 AND
  9223372036854775807
    THEN RAISE EXCEPTION 'AUDIT_INTEGER_REQUIRED'; END IF;
  result:=v::text;
 ELSIF kind IN ('string','boolean','null') THEN result:=v::text;
 ELSE RAISE EXCEPTION 'AUDIT_JSON_REQUIRED'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION audit_digest(ns text, body text) RETURNS text LANGUAGE sql IMMUTABLE
 SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT encode(public.digest(convert_to(ns,'UTF8')||decode('00','hex')||convert_to(body,'UTF8'),
  'sha256'),'hex') $$;
CREATE FUNCTION audit_reset_gate() RETURNS boolean LANGUAGE sql STABLE
 SET search_path=pg_catalog,public,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()
 AND granted AND mode='ExclusiveLock' AND classid=16966::oid AND objid=1145392463::oid
 AND objsubid=1 AND database=(SELECT oid FROM pg_database WHERE datname=current_database())) $$;
CREATE FUNCTION audit_head_json(e public.audit_epochs) RETURNS jsonb LANGUAGE sql STABLE
 SET search_path=pg_catalog,public,pg_temp AS $$ SELECT jsonb_build_object(
 'schema_version','audit-head-v1','canonical_version','audit-canonical-json-v1','simulation',true,
 'user_id',e.user_id,'epoch_id',e.id,'epoch_number',e.epoch_number,'status',e.status,
 'event_count',e.event_count,'last_sequence',e.last_sequence,'last_event_id',e.last_event_id,
 'last_event_hash',e.last_event_hash,'genesis_event_id',e.genesis_event_id,'genesis_event_hash',
  e.genesis_event_hash,
 'previous_epoch_id',e.previous_epoch_id,'previous_seal_hash',e.previous_seal_hash) $$;
CREATE FUNCTION audit_archive_manifest(eid uuid) RETURNS jsonb LANGUAGE sql STABLE
 SET search_path=pg_catalog,public,pg_temp AS $$ SELECT jsonb_build_object(
 'entries',coalesce((SELECT jsonb_agg(jsonb_build_object('kind',kind,'id',entity_id,
  'snapshot_hash',snapshot_hash)
   ORDER BY kind COLLATE "C",entity_id,snapshot_hash COLLATE "C") FROM
  public.audit_subject_snapshots WHERE epoch_id=eid),'[]'::jsonb),
 'counts',coalesce((SELECT jsonb_object_agg(kind,n) FROM (SELECT kind,count(*) AS n FROM
  public.audit_subject_snapshots
   WHERE epoch_id=eid GROUP BY kind) AS counts),'{}'::jsonb)) $$;
CREATE FUNCTION audit_immutable() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN RAISE EXCEPTION 'AUDIT_APPEND_ONLY'; END $$;
CREATE FUNCTION audit_subject_insert() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE body jsonb; tab text; owner uuid; actual uuid;
BEGIN
 body:=NEW.canonical_text::jsonb;
 IF body IS DISTINCT FROM jsonb_build_object(
 'schema_version','audit-subject-v1','canonical_version','audit-canonical-json-v1',
 'simulation',true,'user_id',NEW.user_id,'epoch_id',NEW.epoch_id,'kind',NEW.kind,
 'id',NEW.entity_id,'scope',NEW.scope,'snapshot_version',NEW.snapshot_version,'data',body->'data')
 OR NEW.snapshot_version<>1
 OR body->'data'->>'id' IS DISTINCT FROM NEW.entity_id::text
 THEN RAISE EXCEPTION 'AUDIT_SUBJECT_INVALID'; END IF;
 IF jsonb_typeof(body) IS DISTINCT FROM 'object'
 OR NOT body ?& ARRAY['schema_version','canonical_version','simulation','user_id','epoch_id',
  'kind','id','scope','snapshot_version','data']
 OR body-ARRAY['schema_version','canonical_version','simulation','user_id','epoch_id','kind',
  'id','scope','snapshot_version','data']<>'{}'::jsonb
 OR jsonb_typeof(body->'data') IS DISTINCT FROM 'object'
 OR NOT (body->'data') ? 'id' THEN RAISE EXCEPTION 'AUDIT_SUBJECT_INVALID'; END IF;
 IF body->>'schema_version'<>'audit-subject-v1' OR
  body->>'canonical_version'<>'audit-canonical-json-v1'
 OR body->>'simulation'<>'true' OR (body->>'user_id')::uuid<>NEW.user_id
 OR (body->>'epoch_id')::uuid<>NEW.epoch_id OR (body->>'id')::uuid<>NEW.entity_id
 OR body->>'kind'<>NEW.kind OR body->>'scope'<>NEW.scope OR
  (body->>'snapshot_version')::int<>NEW.snapshot_version
 OR public.audit_digest('bounded-funds/audit-subject-v1',NEW.canonical_text)<>NEW.snapshot_hash
 OR (body->'data'->>'id')::uuid<>NEW.entity_id
 THEN RAISE EXCEPTION 'AUDIT_SUBJECT_INVALID'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.audit_epochs WHERE id=NEW.epoch_id AND user_id=NEW.user_id
  AND status='OPEN')
 THEN RAISE EXCEPTION 'AUDIT_EPOCH_CLOSED'; END IF;
 tab:=CASE NEW.kind WHEN 'USER' THEN 'users' WHEN 'ACCOUNT' THEN 'accounts'
 WHEN 'EVIDENCE' THEN 'evidence_items' WHEN 'TRANSACTION' THEN 'transactions'
 WHEN 'CREDIT_CARD_BILL' THEN 'credit_card_bills' WHEN 'ASSET_PRODUCT' THEN 'asset_products'
 WHEN 'POLICY' THEN 'policies' WHEN 'POLICY_VERSION' THEN 'policy_versions' WHEN
  'POLICY_PROPOSAL' THEN 'policy_proposals'
 WHEN 'GOAL' THEN 'goals' WHEN 'ASSET_POSITION' THEN 'asset_positions' WHEN 'DECISION_RUN' THEN
  'decision_runs'
 WHEN 'DECISION_CONSTRAINT' THEN 'decision_constraints' WHEN 'ACTION_PLAN' THEN 'action_plans'
 WHEN 'ACTION_RECEIPT' THEN 'action_receipts' WHEN 'BANK_REDEMPTION' THEN
  'simulated_bank_redemptions'
 WHEN 'BANK_POSTING' THEN 'simulated_bank_postings' WHEN 'BANK_OPERATION' THEN 'bank_operations'
 WHEN 'RESOURCE_CLAIM' THEN 'action_resource_reservations' ELSE NULL END;
 IF tab IS NULL THEN RAISE EXCEPTION 'AUDIT_SUBJECT_KIND'; END IF;
 IF NEW.kind='ASSET_PRODUCT' THEN
   IF NEW.scope<>'GLOBAL_CATALOG' OR body->'data'->>'user_id' IS NOT NULL
   THEN RAISE EXCEPTION 'AUDIT_SUBJECT_OWNER'; END IF;
   EXECUTE 'SELECT id FROM public.asset_products WHERE id=$1' INTO actual USING NEW.entity_id;
 ELSIF NEW.kind='USER' THEN
   actual:=NEW.entity_id;
   IF NEW.entity_id<>NEW.user_id OR NEW.scope<>'TENANT' THEN RAISE EXCEPTION
  'AUDIT_SUBJECT_OWNER'; END IF;
 ELSE
   IF NEW.scope<>'TENANT' OR body->'data'->>'user_id' IS DISTINCT FROM NEW.user_id::text
   THEN RAISE EXCEPTION
  'AUDIT_SUBJECT_OWNER'; END IF;
   EXECUTE format('SELECT id,user_id FROM public.%I WHERE id=$1',tab) INTO actual,owner USING
  NEW.entity_id;
   IF owner IS DISTINCT FROM NEW.user_id THEN RAISE EXCEPTION 'AUDIT_SUBJECT_OWNER'; END IF;
 END IF;
 IF actual IS NULL THEN RAISE EXCEPTION 'AUDIT_SUBJECT_MISSING'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION audit_epoch_guard() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE prior public.audit_epochs; tail public.audit_events; total bigint; seal jsonb;
BEGIN
 IF TG_OP='INSERT' THEN
  PERFORM 1 FROM public.users WHERE id=NEW.user_id AND is_simulated FOR UPDATE;
  IF NOT FOUND OR NEW.status<>'OPEN' OR NEW.event_count<>0 OR NEW.last_sequence<>0
 OR NEW.last_event_id IS NOT NULL OR NEW.genesis_event_id IS NOT NULL OR NEW.seal_hash IS NOT NULL
 OR NEW.last_event_hash IS NOT NULL OR NEW.genesis_event_hash IS NOT NULL OR NEW.sealed_at IS
  NOT NULL
 OR NEW.seal_canonical_text IS NOT NULL OR NEW.archive_manifest_hash IS NOT NULL OR
  NEW.archive_record_counts<>'{}'::jsonb
 OR NEW.schema_version<>'audit-head-v1' OR NEW.canonical_version<>'audit-canonical-json-v1'
  THEN RAISE EXCEPTION 'AUDIT_EPOCH_INITIALIZATION'; END IF;
  SELECT * INTO prior FROM public.audit_epochs WHERE user_id=NEW.user_id ORDER BY epoch_number
  DESC LIMIT 1 FOR UPDATE;
  IF prior.id IS NULL THEN
   IF NEW.epoch_number<>1 OR NEW.previous_epoch_id IS NOT NULL OR NEW.previous_seal_hash IS NOT NULL
   THEN RAISE EXCEPTION 'AUDIT_EPOCH_ANCESTRY'; END IF;
  ELSIF prior.status<>'SEALED' OR NEW.epoch_number<>prior.epoch_number+1
  OR NEW.previous_epoch_id IS DISTINCT FROM prior.id OR NEW.previous_seal_hash IS DISTINCT FROM
  prior.seal_hash
  OR NEW.user_id<>'271a9827-f82a-5be6-9e0a-50f721029fb0'::uuid OR NOT public.audit_reset_gate()
  THEN RAISE EXCEPTION 'AUDIT_EPOCH_ANCESTRY'; END IF;
  RETURN NEW;
 END IF;
 IF pg_trigger_depth()<>2 OR OLD.status<>'OPEN' OR NEW.event_count<>OLD.event_count+1 OR
  NEW.last_sequence<>NEW.event_count
 OR (to_jsonb(NEW)-ARRAY['event_count','last_sequence','last_event_id','last_event_hash',
  'genesis_event_id','genesis_event_hash',
   'status','sealed_at','seal_canonical_text','seal_hash','archive_manifest_hash',
  'archive_record_counts'])
 IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['event_count','last_sequence','last_event_id',
  'last_event_hash','genesis_event_id','genesis_event_hash',
   'status','sealed_at','seal_canonical_text','seal_hash','archive_manifest_hash',
  'archive_record_counts'])
 THEN RAISE EXCEPTION 'AUDIT_HEAD_WRITE_FORBIDDEN'; END IF;
 SELECT * INTO tail FROM public.audit_events WHERE id=NEW.last_event_id AND user_id=NEW.user_id
  AND epoch_id=NEW.id;
 SELECT count(*) INTO total FROM public.audit_events WHERE epoch_id=NEW.id;
 IF tail.id IS NULL OR tail.sequence_number<>NEW.last_sequence OR
  tail.event_hash<>NEW.last_event_hash
 OR tail.previous_hash IS DISTINCT FROM OLD.last_event_hash OR total<>NEW.event_count
 OR NEW.genesis_event_id IS DISTINCT FROM coalesce(OLD.genesis_event_id,tail.id)
 OR NEW.genesis_event_hash IS DISTINCT FROM coalesce(OLD.genesis_event_hash,tail.event_hash)
 THEN RAISE EXCEPTION 'AUDIT_HEAD_INCONSISTENT'; END IF;
 IF tail.event_type='EPOCH_SEALED' THEN
  seal:=NEW.seal_canonical_text::jsonb;
  IF NEW.status<>'SEALED' OR seal->'head' IS DISTINCT FROM public.audit_head_json(NEW)
  OR seal->>'seal_hash'<>NEW.seal_hash OR public.audit_canonical(seal)<>NEW.seal_canonical_text
  OR public.audit_digest('bounded-funds/audit-epoch-seal-v1',
  public.audit_canonical(seal-'seal_hash'))<>NEW.seal_hash
  OR NEW.archive_manifest_hash<>public.audit_digest('bounded-funds/audit-archive-v1',
  public.audit_canonical(public.audit_archive_manifest(NEW.id)))
  OR NEW.archive_record_counts IS DISTINCT FROM public.audit_archive_manifest(NEW.id)->'counts'
  THEN RAISE EXCEPTION 'AUDIT_SEAL_INCONSISTENT'; END IF;
 ELSIF NEW.status<>'OPEN' OR NEW.seal_hash IS NOT NULL OR NEW.seal_canonical_text IS NOT NULL
 OR NEW.sealed_at IS NOT NULL OR NEW.archive_manifest_hash IS DISTINCT FROM
  OLD.archive_manifest_hash
 OR NEW.archive_record_counts IS DISTINCT FROM OLD.archive_record_counts
 THEN RAISE EXCEPTION 'AUDIT_HEAD_WRITE_FORBIDDEN'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION audit_event_insert() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE body jsonb; e public.audit_epochs; ref jsonb; required_kind text; required_id uuid;
  cause public.audit_events;
BEGIN
 IF NEW.epoch_id IS NULL THEN RAISE EXCEPTION 'AUDIT_LEGACY_INSERT_FORBIDDEN'; END IF;
 PERFORM 1 FROM public.users WHERE id=NEW.user_id AND is_simulated FOR UPDATE;
 SELECT * INTO e FROM public.audit_epochs WHERE id=NEW.epoch_id AND user_id=NEW.user_id FOR UPDATE;
 IF e.id IS NULL OR e.status<>'OPEN' THEN RAISE EXCEPTION 'AUDIT_EPOCH_CLOSED'; END IF;
 body:=NEW.canonical_text::jsonb;
 IF NEW.schema_version IS DISTINCT FROM 'audit-event-v1'
 OR NEW.canonical_version IS DISTINCT FROM 'audit-canonical-json-v1'
 OR body IS DISTINCT FROM jsonb_build_object(
 'schema_version','audit-event-v1','canonical_version','audit-canonical-json-v1',
 'simulation',true,'id',NEW.id,'user_id',NEW.user_id,'epoch_id',NEW.epoch_id,
 'sequence_number',NEW.sequence_number,'previous_hash',NEW.previous_hash,
 'event_type',NEW.event_type,'aggregate_type',NEW.aggregate_type,'aggregate_id',NEW.aggregate_id,
 'correlation_id',NEW.correlation_id,'causation_id',NEW.causation_id,
 'decision_run_id',NEW.decision_run_id,'action_plan_id',NEW.action_plan_id,
 'action_receipt_id',NEW.action_receipt_id,'idempotency_key',NEW.idempotency_key,
 'payload_version',NEW.payload_version,'payload',NEW.payload,'event_hash',NEW.event_hash,
 'occurred_at',to_char(NEW.occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
 'observed_at',to_char(NEW.observed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
 'appended_at',to_char(NEW.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
 THEN RAISE EXCEPTION 'AUDIT_EVENT_INVALID'; END IF;
 IF jsonb_typeof(body) IS DISTINCT FROM 'object'
 OR NOT body ?& ARRAY['schema_version','canonical_version','simulation','id','user_id',
  'epoch_id','sequence_number','previous_hash',
 'event_type','aggregate_type','aggregate_id','correlation_id','causation_id','decision_run_id',
  'action_plan_id','action_receipt_id',
 'idempotency_key','payload_version','payload','occurred_at','observed_at','appended_at',
  'event_hash']
 OR body-ARRAY['schema_version','canonical_version','simulation','id','user_id','epoch_id',
  'sequence_number','previous_hash',
 'event_type','aggregate_type','aggregate_id','correlation_id','causation_id','decision_run_id',
  'action_plan_id','action_receipt_id',
 'idempotency_key','payload_version','payload','occurred_at','observed_at','appended_at',
  'event_hash']<>'{}'::jsonb
 OR jsonb_typeof(body->'payload') IS DISTINCT FROM 'object'
 OR NOT (body->'payload') ?& ARRAY['fact_key','correlation_kind','references','anchors',
  'changes','missing_evidence_ids',
 'legacy_origin','epoch_transition','observation','context']
 OR jsonb_typeof(body->'payload'->'references') IS DISTINCT FROM 'array'
 OR coalesce(length(body->'payload'->>'fact_key'),0) NOT BETWEEN 1 AND 160
 OR NEW.occurred_at>NEW.observed_at
 THEN RAISE EXCEPTION 'AUDIT_EVENT_INVALID'; END IF;
 IF public.audit_canonical(body)<>NEW.canonical_text
 OR public.audit_digest('bounded-funds/audit-event-v1',
  public.audit_canonical(body-'event_hash'))<>NEW.event_hash
 OR body->>'event_hash'<>NEW.event_hash OR body->>'schema_version'<>'audit-event-v1'
 OR body->>'canonical_version'<>'audit-canonical-json-v1' OR body->>'simulation'<>'true'
 OR (body->>'id')::uuid<>NEW.id OR (body->>'user_id')::uuid<>NEW.user_id OR
  (body->>'epoch_id')::uuid<>NEW.epoch_id
 OR (body->>'sequence_number')::int<>NEW.sequence_number OR body->>'previous_hash' IS DISTINCT
  FROM NEW.previous_hash
 OR body->>'event_type'<>NEW.event_type OR body->>'aggregate_type'<>NEW.aggregate_type
 OR (body->>'aggregate_id')::uuid<>NEW.aggregate_id OR
  (body->>'correlation_id')::uuid<>NEW.correlation_id
 OR (body->>'causation_id')::uuid IS DISTINCT FROM NEW.causation_id
 OR (body->>'decision_run_id')::uuid IS DISTINCT FROM NEW.decision_run_id
 OR (body->>'action_plan_id')::uuid IS DISTINCT FROM NEW.action_plan_id
 OR (body->>'action_receipt_id')::uuid IS DISTINCT FROM NEW.action_receipt_id
 OR body->>'idempotency_key'<>NEW.idempotency_key OR
  (body->>'payload_version')::int<>NEW.payload_version
 OR body->'payload' IS DISTINCT FROM NEW.payload OR
  (body->>'occurred_at')::timestamptz<>NEW.occurred_at
 OR (body->>'observed_at')::timestamptz<>NEW.observed_at OR
  (body->>'appended_at')::timestamptz<>NEW.created_at
 OR NEW.sequence_number<>e.last_sequence+1 OR NEW.previous_hash IS DISTINCT FROM e.last_event_hash
 THEN RAISE EXCEPTION 'AUDIT_EVENT_INVALID'; END IF;
 IF NEW.event_type NOT IN ('EPOCH_STARTED','EPOCH_SEALED','DECISION_RECORDED','ACTION_CREATED',
  'ACTION_STATE_CHANGED',
 'BANK_ACCEPTED','BANK_SETTLED','ACTION_PROJECTED','RECOVERY_OBSERVED',
  'POLICY_VERSION_CONFIRMED','POLICY_STATE_CHANGED','GOAL_INITIALIZED')
 OR NEW.payload_version<>1 THEN RAISE EXCEPTION 'AUDIT_PROTOCOL_UNSUPPORTED'; END IF;
 IF (e.event_count=0) IS DISTINCT FROM (NEW.event_type='EPOCH_STARTED') THEN RAISE EXCEPTION
  'AUDIT_GENESIS_REQUIRED'; END IF;
 IF NEW.event_type IN ('EPOCH_STARTED','EPOCH_SEALED') THEN
  IF NEW.aggregate_type<>'EPOCH' OR NEW.aggregate_id<>e.id OR NEW.correlation_id<>e.id
  OR NEW.payload->>'correlation_kind'<>'EPOCH' THEN RAISE EXCEPTION 'AUDIT_EPOCH_SUBJECT'; END IF;
  IF NEW.causation_id IS NOT NULL OR NEW.decision_run_id IS NOT NULL OR NEW.action_plan_id IS
  NOT NULL
  OR NEW.action_receipt_id IS NOT NULL THEN RAISE EXCEPTION 'AUDIT_EPOCH_SUBJECT'; END IF;
  IF NEW.event_type='EPOCH_STARTED' AND (
   NEW.payload->'epoch_transition'->>'kind' IS DISTINCT FROM CASE WHEN e.epoch_number=1 THEN
  'INIT' ELSE 'RESET' END
   OR (NEW.payload->'epoch_transition'->>'previous_epoch_id')::uuid IS DISTINCT FROM
  e.previous_epoch_id
   OR NEW.payload->'epoch_transition'->>'previous_seal_hash' IS DISTINCT FROM e.previous_seal_hash
   OR (e.epoch_number>1 AND (NEW.payload->'epoch_transition'->>'dataset_hash' IS NULL
     OR NEW.payload->'epoch_transition'->>'reset_key' IS NULL OR
  NEW.payload->'epoch_transition'->>'reason' IS NULL
     OR NEW.payload->'epoch_transition'->>'principal' IS NULL)))
  THEN RAISE EXCEPTION 'AUDIT_EPOCH_ANCESTRY'; END IF;
 ELSE
  FOR required_kind,required_id IN VALUES (NEW.aggregate_type,NEW.aggregate_id),
    (NEW.payload->>'correlation_kind',NEW.correlation_id),('DECISION_RUN',NEW.decision_run_id),
    ('ACTION_PLAN',NEW.action_plan_id),('ACTION_RECEIPT',NEW.action_receipt_id)
  LOOP
   IF required_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM
  jsonb_array_elements(NEW.payload->'references') r
     WHERE r->>'kind'=required_kind AND (r->>'id')::uuid=required_id)
   THEN RAISE EXCEPTION 'AUDIT_REFERENCE_MISSING'; END IF;
  END LOOP;
 END IF;
 FOR ref IN SELECT value FROM jsonb_array_elements(NEW.payload->'references') LOOP
  IF (ref->>'scope'='TENANT' AND (ref->>'user_id')::uuid IS DISTINCT FROM NEW.user_id)
   OR (ref->>'scope'='GLOBAL_CATALOG' AND (ref->>'kind'<>'ASSET_PRODUCT' OR ref->>'user_id' IS
  NOT NULL))
   OR NOT EXISTS(SELECT 1 FROM public.audit_subject_snapshots s WHERE s.user_id=NEW.user_id AND
  s.epoch_id=NEW.epoch_id
      AND s.kind=ref->>'kind' AND s.entity_id=(ref->>'id')::uuid AND
  s.snapshot_hash=ref->>'snapshot_hash'
      AND s.scope=ref->>'scope' AND s.snapshot_version=(ref->>'snapshot_version')::int)
  THEN RAISE EXCEPTION 'AUDIT_REFERENCE_INVALID'; END IF;
 END LOOP;
 IF NEW.causation_id IS NOT NULL THEN
  SELECT * INTO cause FROM public.audit_events WHERE id=NEW.causation_id AND
  user_id=NEW.user_id AND epoch_id=NEW.epoch_id;
  IF cause.id IS NULL OR cause.sequence_number>=NEW.sequence_number OR
  cause.correlation_id<>NEW.correlation_id
  THEN RAISE EXCEPTION 'AUDIT_CAUSATION_INVALID'; END IF;
 END IF;
 IF NEW.event_type='EPOCH_SEALED' AND (NEW.user_id<>'271a9827-f82a-5be6-9e0a-50f721029fb0'::uuid
   OR NOT public.audit_reset_gate() OR NEW.payload->'epoch_transition'->>'kind'<>'SEAL'
   OR NEW.payload->'epoch_transition'->'pre_seal_head' IS DISTINCT FROM public.audit_head_json(e)
   OR NEW.payload->'epoch_transition'->'archive_record_counts' IS DISTINCT FROM
  public.audit_archive_manifest(e.id)->'counts'
   OR NEW.payload->'epoch_transition'->>'reset_key' IS NULL OR
  NEW.payload->'epoch_transition'->>'reason' IS NULL
   OR NEW.payload->'epoch_transition'->>'principal' IS NULL
   OR NEW.payload->'epoch_transition'->>'archive_manifest_hash' IS DISTINCT FROM
     public.audit_digest('bounded-funds/audit-archive-v1',
  public.audit_canonical(public.audit_archive_manifest(e.id))))
 THEN RAISE EXCEPTION 'AUDIT_RESET_INVALID'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION audit_event_head() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE e public.audit_epochs; transition jsonb; seal jsonb; new_seal_text text; new_seal_hash
  text; head jsonb;
BEGIN
 SELECT * INTO e FROM public.audit_epochs WHERE id=NEW.epoch_id;
 IF NEW.event_type='EPOCH_SEALED' THEN
  transition:=NEW.payload->'epoch_transition';
  head:=public.audit_head_json(e)||jsonb_build_object('status','SEALED','event_count',
  NEW.sequence_number,
    'last_sequence',NEW.sequence_number,'last_event_id',NEW.id,'last_event_hash',NEW.event_hash);
  seal:=jsonb_build_object('schema_version','audit-epoch-seal-v1','canonical_version',
  'audit-canonical-json-v1',
    'simulation',true,'user_id',e.user_id,'epoch_id',e.id,'epoch_number',e.epoch_number,'head',head,
    'previous_epoch_id',e.previous_epoch_id,'previous_seal_hash',e.previous_seal_hash,
    'archive_manifest_hash',transition->'archive_manifest_hash','archive_record_counts',
  transition->'archive_record_counts',
    'seed_version',transition->'seed_version','summary_version',transition->'summary_version',
  'dataset_hash',transition->'dataset_hash',
    'reset_key',transition->'reset_key','reason',transition->'reason','principal',
  transition->'principal','sealed_at',
    to_char(NEW.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
  new_seal_hash:=public.audit_digest('bounded-funds/audit-epoch-seal-v1',
  public.audit_canonical(seal));
  new_seal_text:=public.audit_canonical(seal||jsonb_build_object('seal_hash',new_seal_hash));
 END IF;
 UPDATE public.audit_epochs SET event_count=NEW.sequence_number,last_sequence=NEW.sequence_number,
 last_event_id=NEW.id,last_event_hash=NEW.event_hash,genesis_event_id=coalesce(genesis_event_id,
  NEW.id),
 genesis_event_hash=coalesce(genesis_event_hash,NEW.event_hash),
 status=CASE WHEN NEW.event_type='EPOCH_SEALED' THEN 'SEALED' ELSE 'OPEN' END,
 sealed_at=CASE WHEN NEW.event_type='EPOCH_SEALED' THEN NEW.created_at ELSE NULL END,
 seal_canonical_text=new_seal_text,seal_hash=new_seal_hash,
 archive_manifest_hash=CASE WHEN NEW.event_type='EPOCH_SEALED' THEN
  transition->>'archive_manifest_hash' ELSE NULL END,
 archive_record_counts=CASE WHEN NEW.event_type='EPOCH_SEALED' THEN
  transition->'archive_record_counts' ELSE '{}'::jsonb END
 WHERE id=NEW.epoch_id;
 RETURN NEW;
END $$;
CREATE FUNCTION audit_epoch_complete() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE e public.audit_epochs; n bigint; first public.audit_events; tail public.audit_events;
BEGIN
 SELECT * INTO e FROM public.audit_epochs WHERE id=NEW.id;
 SELECT count(*) INTO n FROM public.audit_events WHERE epoch_id=e.id;
 SELECT * INTO first FROM public.audit_events WHERE epoch_id=e.id AND sequence_number=1;
 SELECT * INTO tail FROM public.audit_events WHERE epoch_id=e.id ORDER BY sequence_number DESC
  LIMIT 1;
 IF e.id IS NULL OR n=0 OR n<>e.event_count OR tail.sequence_number<>e.last_sequence
 OR tail.id<>e.last_event_id OR tail.event_hash<>e.last_event_hash OR
  first.event_type<>'EPOCH_STARTED'
 OR first.id<>e.genesis_event_id OR first.event_hash<>e.genesis_event_hash
 THEN RAISE EXCEPTION 'AUDIT_HEAD_INCOMPLETE'; END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER audit_event_insert BEFORE INSERT ON audit_events FOR EACH ROW EXECUTE FUNCTION
  audit_event_insert();
CREATE TRIGGER audit_event_head AFTER INSERT ON audit_events FOR EACH ROW EXECUTE FUNCTION
  audit_event_head();
CREATE TRIGGER audit_epoch_guard BEFORE INSERT OR UPDATE ON audit_epochs FOR EACH ROW EXECUTE
  FUNCTION audit_epoch_guard();
CREATE CONSTRAINT TRIGGER audit_epoch_complete AFTER INSERT OR UPDATE ON audit_epochs
  DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION audit_epoch_complete();
CREATE TRIGGER audit_subject_insert BEFORE INSERT ON audit_subject_snapshots FOR EACH ROW
  EXECUTE FUNCTION audit_subject_insert();
CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW
  EXECUTE FUNCTION audit_immutable();
CREATE TRIGGER audit_events_truncate BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT EXECUTE
  FUNCTION audit_immutable();
CREATE TRIGGER audit_snapshots_immutable BEFORE UPDATE OR DELETE ON audit_subject_snapshots FOR
  EACH ROW EXECUTE FUNCTION audit_immutable();
CREATE TRIGGER audit_snapshots_truncate BEFORE TRUNCATE ON audit_subject_snapshots FOR EACH
  STATEMENT EXECUTE FUNCTION audit_immutable();
CREATE TRIGGER audit_epochs_immutable BEFORE DELETE ON audit_epochs FOR EACH ROW EXECUTE
  FUNCTION audit_immutable();
CREATE TRIGGER audit_epochs_truncate BEFORE TRUNCATE ON audit_epochs FOR EACH STATEMENT EXECUTE
  FUNCTION audit_immutable();
REVOKE ALL ON FUNCTION audit_event_head() FROM PUBLIC;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    for name, kind in [
        ("epoch_id", sa.UUID()),
        ("schema_version", sa.String(48)),
        ("canonical_version", sa.String(48)),
        ("canonical_text", sa.Text()),
    ]:
        op.add_column("audit_events", sa.Column(name, kind, nullable=True))
    for name in [
        "uq_audit_events_user_sequence",
        "uq_audit_events_user_idempotency",
        "uq_audit_events_user_hash",
    ]:
        op.drop_constraint(name, "audit_events", type_="unique")
    for column, table in [
        ("decision_run_id", "decision_runs"),
        ("action_plan_id", "action_plans"),
        ("action_receipt_id", "action_receipts"),
    ]:
        op.drop_constraint(f"fk_audit_events_{column}_{table}", "audit_events", type_="foreignkey")
    for name, columns in [
        ("identity", ["id", "user_id", "epoch_id"]),
        ("sequence", ["user_id", "epoch_id", "sequence_number"]),
        ("key", ["user_id", "epoch_id", "idempotency_key"]),
        ("hash", ["user_id", "epoch_id", "event_hash"]),
    ]:
        op.create_unique_constraint(f"uq_audit_events_epoch_{name}", "audit_events", columns)
    op.execute(TABLES)
    op.create_foreign_key(
        "fk_audit_events_epoch",
        "audit_events",
        "audit_epochs",
        ["epoch_id", "user_id"],
        ["id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_audit_events_epoch_causation",
        "audit_events",
        "audit_events",
        ["causation_id", "user_id", "epoch_id"],
        ["id", "user_id", "epoch_id"],
        ondelete="RESTRICT",
    )
    for name, column in [("tail", "last_event_id"), ("genesis", "genesis_event_id")]:
        op.create_foreign_key(
            f"fk_audit_epochs_{name}",
            "audit_epochs",
            "audit_events",
            [column, "user_id", "id"],
            ["id", "user_id", "epoch_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )
    for name, column in [
        ("sequence", "sequence_number"),
        ("key", "idempotency_key"),
        ("hash", "event_hash"),
    ]:
        op.create_index(
            f"uq_audit_events_legacy_{name}",
            "audit_events",
            ["user_id", column],
            unique=True,
            postgresql_where=sa.text("epoch_id IS NULL"),
        )
    op.create_index(
        "uq_audit_events_fact",
        "audit_events",
        ["user_id", "epoch_id", "event_type", sa.text("(payload->>'fact_key')")],
        unique=True,
        postgresql_where=sa.text("epoch_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "envelope",
        "audit_events",
        "(epoch_id IS NULL AND schema_version IS NULL AND canonical_version IS NULL "
        "AND canonical_text IS NULL) OR (epoch_id IS NOT NULL AND schema_version='audit-event-v1' "
        "AND canonical_version='audit-canonical-json-v1' AND canonical_text IS NOT NULL "
        "AND octet_length(canonical_text)<=1048576)",
    )
    op.execute(FUNCTIONS)


def downgrade() -> None:
    if op.get_context().as_sql:
        raise ValueError("Offline audit downgrade is disabled")
    database = op.get_bind().engine.url.database
    if database is None or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None:
        raise ValueError("Audit downgrade requires a generated bf_test_ database")
    if any(
        op.get_bind().execute(sa.text(f"SELECT EXISTS(SELECT 1 FROM {table})")).scalar()
        for table in ["audit_events", "audit_epochs", "audit_subject_snapshots"]
    ):
        raise ValueError("Audit history cannot be discarded by downgrade")
    op.execute(
        "DROP TRIGGER audit_event_insert ON audit_events; "
        "DROP TRIGGER audit_event_head ON audit_events; "
        "DROP TRIGGER audit_events_immutable ON audit_events; "
        "DROP TRIGGER audit_events_truncate ON audit_events"
    )
    op.drop_constraint("fk_audit_epochs_tail", "audit_epochs", type_="foreignkey")
    op.drop_constraint("fk_audit_epochs_genesis", "audit_epochs", type_="foreignkey")
    op.drop_constraint("fk_audit_events_epoch", "audit_events", type_="foreignkey")
    op.drop_constraint("fk_audit_events_epoch_causation", "audit_events", type_="foreignkey")
    op.drop_table("audit_subject_snapshots")
    op.execute(
        "DROP FUNCTION audit_head_json(audit_epochs); DROP FUNCTION audit_archive_manifest(uuid)"
    )
    op.drop_table("audit_epochs")
    for signature in [
        "audit_epoch_complete()",
        "audit_epoch_guard()",
        "audit_event_insert()",
        "audit_event_head()",
        "audit_subject_insert()",
        "audit_immutable()",
        "audit_reset_gate()",
        "audit_digest(text,text)",
        "audit_canonical(jsonb,integer)",
    ]:
        op.execute(f"DROP FUNCTION {signature}")
    op.drop_constraint(op.f("ck_audit_events_envelope"), "audit_events", type_="check")
    for name in ["identity", "sequence", "key", "hash"]:
        op.drop_constraint(f"uq_audit_events_epoch_{name}", "audit_events", type_="unique")
    for name in ["sequence", "key", "hash"]:
        op.drop_index(f"uq_audit_events_legacy_{name}", table_name="audit_events")
    op.drop_index("uq_audit_events_fact", table_name="audit_events")
    for name in ["epoch_id", "schema_version", "canonical_version", "canonical_text"]:
        op.drop_column("audit_events", name)
    for name, column in [
        ("sequence", "sequence_number"),
        ("idempotency", "idempotency_key"),
        ("hash", "event_hash"),
    ]:
        op.create_unique_constraint(
            f"uq_audit_events_user_{name}", "audit_events", ["user_id", column]
        )
    for column, table in [
        ("decision_run_id", "decision_runs"),
        ("action_plan_id", "action_plans"),
        ("action_receipt_id", "action_receipts"),
    ]:
        op.create_foreign_key(
            f"fk_audit_events_{column}_{table}",
            "audit_events",
            table,
            [column, "user_id"],
            ["id", "user_id"],
            ondelete="RESTRICT",
        )
