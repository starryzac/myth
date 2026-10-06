"""External simulated bank facts with immutable legacy posting/audit originals."""

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0007_external_bank_facts"
down_revision: str | Sequence[str] | None = "0006_audit_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = r"""
CREATE TABLE external_bank_facts (
    protocol_version VARCHAR(40) NOT NULL,
    source_id VARCHAR(40) NOT NULL,
    external_ref VARCHAR(160) NOT NULL,
    kind VARCHAR(24) NOT NULL,
    account_id UUID NOT NULL,
    amount_cents BIGINT NOT NULL,
    currency VARCHAR(3) DEFAULT 'CNY' NOT NULL,
    counterparty_ref VARCHAR(96) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    idempotency_key VARCHAR(160) NOT NULL,
    request JSONB NOT NULL,
    request_canonical_text TEXT NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    bank_status VARCHAR(24) DEFAULT 'ACCEPTED' NOT NULL,
    accepted_at TIMESTAMP WITH TIME ZONE NOT NULL,
    settled_at TIMESTAMP WITH TIME ZONE,
    bank_result JSONB,
    bank_result_canonical_text TEXT,
    bank_result_hash VARCHAR(64),
    projection_status VARCHAR(24) DEFAULT 'PENDING' NOT NULL,
    projected_at TIMESTAMP WITH TIME ZONE,
    projection_result JSONB,
    projection_result_canonical_text TEXT,
    projection_result_hash VARCHAR(64),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    user_id UUID NOT NULL,
    id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_external_bank_facts PRIMARY KEY (id),
    CONSTRAINT fk_external_bank_facts_account_id_accounts FOREIGN KEY(account_id, user_id)
 REFERENCES accounts (id, user_id) ON DELETE RESTRICT,
    CONSTRAINT uq_external_bank_fact_ref UNIQUE (user_id, source_id, external_ref),
    CONSTRAINT uq_external_bank_fact_key UNIQUE (user_id, idempotency_key),
    CONSTRAINT ck_external_bank_facts_protocol CHECK (protocol_version = 'bank-external-fact-v1'
 AND source_id = 'bounded-funds-external-v1'),
    CONSTRAINT ck_external_bank_facts_economics CHECK (kind IN ('INCOME', 'CONSUMPTION') AND
 currency = 'CNY' AND amount_cents > 0),
    CONSTRAINT ck_external_bank_facts_identity CHECK (length(external_ref) BETWEEN 1 AND 160 AND
 length(idempotency_key) BETWEEN 1 AND 160 AND length(counterparty_ref) BETWEEN 1 AND 96),
    CONSTRAINT ck_external_bank_facts_time CHECK (occurred_at <= observed_at AND observed_at <=
 accepted_at AND updated_at >= accepted_at),
    CONSTRAINT ck_external_bank_facts_request CHECK (request_hash ~ '^[0-9a-f]{64}$' AND
 jsonb_typeof(request) = 'object' AND octet_length(request_canonical_text) <= 1048576),
    CONSTRAINT ck_external_bank_facts_bank_status CHECK (bank_status IN ('ACCEPTED', 'SETTLED',
 'UNKNOWN', 'REJECTED')),
    CONSTRAINT ck_external_bank_facts_bank_result CHECK ((bank_status = 'SETTLED' AND settled_at
 IS NOT NULL AND settled_at >= accepted_at AND bank_result IS NOT NULL AND
 jsonb_typeof(bank_result) = 'object' AND bank_result_canonical_text IS NOT NULL AND
 bank_result_hash IS NOT NULL AND bank_result_hash ~ '^[0-9a-f]{64}$' AND
 octet_length(bank_result_canonical_text) <= 1048576) OR (bank_status <> 'SETTLED' AND
 settled_at IS NULL AND bank_result IS NULL AND bank_result_canonical_text IS NULL AND
 bank_result_hash IS NULL)),
    CONSTRAINT ck_external_bank_facts_projection_status CHECK (projection_status IN ('PENDING',
 'PROJECTED', 'UNKNOWN')),
    CONSTRAINT ck_external_bank_facts_projection_result CHECK ((projection_status = 'PROJECTED'
 AND bank_status = 'SETTLED' AND projected_at IS NOT NULL AND projected_at >= settled_at AND
 projection_result IS NOT NULL AND jsonb_typeof(projection_result) = 'object' AND
 projection_result_canonical_text IS NOT NULL AND projection_result_hash IS NOT NULL AND
 projection_result_hash ~ '^[0-9a-f]{64}$' AND octet_length(projection_result_canonical_text) <=
 1048576) OR (projection_status <> 'PROJECTED' AND projected_at IS NULL AND projection_result IS
 NULL AND projection_result_canonical_text IS NULL AND projection_result_hash IS NULL)),
    CONSTRAINT ck_external_bank_facts_projection_bank_state CHECK ((projection_status =
 'PENDING' OR bank_status = 'SETTLED') AND updated_at >= coalesce(projected_at, settled_at,
 accepted_at)),
    CONSTRAINT uq_external_bank_facts_id UNIQUE (id, user_id),
    CONSTRAINT fk_external_bank_facts_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
 ON DELETE RESTRICT
)

;
CREATE INDEX ix_external_bank_facts_user_id ON external_bank_facts (user_id);
CREATE INDEX ix_external_bank_fact_pending ON external_bank_facts (user_id, bank_status,
 projection_status, occurred_at, id);
"""

POSTING_DDL = r"""
ALTER TABLE simulated_bank_postings ADD COLUMN external_fact_id UUID;
ALTER TABLE simulated_bank_postings DROP CONSTRAINT ck_simulated_bank_postings_entry, DROP
 CONSTRAINT ck_simulated_bank_postings_ledger_identity;
ALTER TABLE simulated_bank_postings ADD CONSTRAINT ck_simulated_bank_postings_ledger_identity
 CHECK ((ledger_dimension = 'ECONOMIC' AND ((account_id IS NOT NULL AND position_id IS NULL AND
 ledger_key = 'CASH:' || account_id::text) OR (account_id IS NULL AND position_id IS NOT NULL
 AND ledger_key = 'POSITION:' || position_id::text) OR (account_id IS NULL AND position_id IS
 NULL AND (ledger_key LIKE 'PAYEE:%%' OR ledger_key LIKE 'FEE:%%' OR ledger_key LIKE 'LOSS:%%'
 OR ledger_key LIKE 'CLEARING:bounded-funds-external-v1:%%')))) OR (ledger_dimension IN
 ('GOAL_OWNERSHIP', 'INCOME_LOCATION', 'LIABILITY') AND position_id IS NULL));
ALTER TABLE simulated_bank_postings ADD CONSTRAINT uq_bank_posting_external_leg UNIQUE
 (external_fact_id, leg_ref);
ALTER TABLE simulated_bank_postings ADD CONSTRAINT ck_simulated_bank_postings_entry CHECK
 ((entry_kind = 'OPENING' AND redemption_id IS NULL AND operation_id IS NULL AND
 external_fact_id IS NULL AND leg_ref IS NULL AND sequence_number = 1 AND previous_posting_id IS
 NULL AND balance_before_cents = 0 AND delta_cents >= 0) OR (entry_kind <> 'OPENING' AND
 ((operation_id IS NOT NULL AND external_fact_id IS NULL) OR (operation_id IS NULL AND
 external_fact_id IS NOT NULL AND redemption_id IS NULL)) AND leg_ref IS NOT NULL AND
 previous_posting_id IS NOT NULL AND sequence_number > 1));
ALTER TABLE simulated_bank_postings ADD CONSTRAINT
 fk_simulated_bank_postings_external_fact_id_external_bank_facts FOREIGN KEY(external_fact_id,
 user_id) REFERENCES external_bank_facts (id, user_id) ON DELETE RESTRICT;
"""

AUDIT_COMPAT = r"""
CREATE OR REPLACE FUNCTION audit_subject_insert() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE body jsonb; tab text; owner uuid; actual uuid;
BEGIN
 body:=NEW.canonical_text::jsonb;
 IF body IS DISTINCT FROM jsonb_build_object(
 'schema_version','audit-subject-v1','canonical_version','audit-canonical-json-v1',
 'simulation',true,'user_id',NEW.user_id,'epoch_id',NEW.epoch_id,'kind',NEW.kind,
 'id',NEW.entity_id,'scope',NEW.scope,'snapshot_version',NEW.snapshot_version,'data',body->'data')
 OR NOT (NEW.snapshot_version=1 OR (NEW.kind='BANK_POSTING' AND NEW.snapshot_version=2))
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
 WHEN 'BANK_EXTERNAL_FACT' THEN 'external_bank_facts' WHEN 'BANK_POSTING' THEN
 'simulated_bank_postings' WHEN 'BANK_OPERATION' THEN 'bank_operations'
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

 IF NEW.kind='BANK_POSTING' THEN
   IF NEW.snapshot_version=1 AND ((body->'data') ? 'external_fact_id'
     OR body->'data'->>'ledger_key' LIKE 'CLEARING:%') THEN
     RAISE EXCEPTION 'AUDIT_POSTING_LAYOUT_INVALID';
   ELSIF NEW.snapshot_version=2 AND (NOT (body->'data') ? 'external_fact_id'
     OR NOT ((body->'data'->>'external_fact_id') IS NOT NULL
       OR body->'data'->>'ledger_key' LIKE 'CLEARING:bounded-funds-external-v1:%')) THEN
     RAISE EXCEPTION 'AUDIT_POSTING_LAYOUT_INVALID';
   END IF;
 END IF;
 IF actual IS NULL THEN RAISE EXCEPTION 'AUDIT_SUBJECT_MISSING'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION audit_event_insert() RETURNS trigger LANGUAGE plpgsql
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
 OR (NEW.payload_version=1 AND NOT (body->'payload') ?& ARRAY['fact_key','correlation_kind',
 'references','anchors',
  'changes','missing_evidence_ids',
 'legacy_origin','epoch_transition','observation','context'])
 OR (NEW.payload_version=2 AND (NOT (body->'payload') ?&
   ARRAY['fact_key','correlation_kind','references','anchors','context']
   OR (body->'payload')-ARRAY['fact_key','correlation_kind','references','anchors',
 'context']<>'{}'::jsonb))
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
 IF NOT ((NEW.payload_version=1 AND NEW.event_type IN ('EPOCH_STARTED','EPOCH_SEALED',
 'DECISION_RECORDED','ACTION_CREATED',
  'ACTION_STATE_CHANGED',
 'BANK_ACCEPTED','BANK_SETTLED','ACTION_PROJECTED','RECOVERY_OBSERVED',
  'POLICY_VERSION_CONFIRMED','POLICY_STATE_CHANGED','GOAL_INITIALIZED'))
 OR (NEW.payload_version=2 AND NEW.event_type IN ('EXTERNAL_BANK_FACT_SETTLED',
 'EXTERNAL_BANK_FACT_PROJECTED'))) THEN RAISE EXCEPTION 'AUDIT_PROTOCOL_UNSUPPORTED'; END IF;
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
    (CASE WHEN NEW.payload->>'correlation_kind'='EXTERNAL_BANK_FACT' THEN 'BANK_EXTERNAL_FACT'
 ELSE NEW.payload->>'correlation_kind' END,NEW.correlation_id),('DECISION_RUN',NEW.decision_run_id),
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

 IF NEW.payload_version=2 THEN
   IF NEW.aggregate_type<>'BANK_EXTERNAL_FACT' OR NEW.aggregate_id<>NEW.correlation_id
     OR NEW.payload->>'correlation_kind'<>'EXTERNAL_BANK_FACT'
     OR NEW.decision_run_id IS NOT NULL OR NEW.action_plan_id IS NOT NULL
     OR NEW.action_receipt_id IS NOT NULL THEN RAISE EXCEPTION 'AUDIT_EXTERNAL_SUBJECT'; END IF;
   IF NEW.event_type='EXTERNAL_BANK_FACT_SETTLED' AND NEW.causation_id IS NOT NULL
     THEN RAISE EXCEPTION 'AUDIT_EXTERNAL_CAUSATION'; END IF;
   IF NEW.event_type='EXTERNAL_BANK_FACT_PROJECTED' AND
     (NEW.causation_id IS NULL OR cause.event_type<>'EXTERNAL_BANK_FACT_SETTLED'
       OR cause.aggregate_type<>'BANK_EXTERNAL_FACT' OR cause.aggregate_id<>NEW.aggregate_id)
     THEN RAISE EXCEPTION 'AUDIT_EXTERNAL_CAUSATION'; END IF;
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
"""

EXTERNAL_GUARDS = r"""
CREATE FUNCTION protect_external_bank_fact() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE mutable text[]:=ARRAY['bank_status','settled_at','bank_result',
 'bank_result_canonical_text','bank_result_hash','projection_status','projected_at',
 'projection_result','projection_result_canonical_text','projection_result_hash','updated_at'];
 bank_fields text[]:=ARRAY['bank_status','settled_at','bank_result',
 'bank_result_canonical_text','bank_result_hash'];
 projection_fields text[]:=ARRAY['projection_status','projected_at','projection_result',
 'projection_result_canonical_text','projection_result_hash'];
BEGIN
 IF NEW.request_canonical_text IS DISTINCT FROM public.audit_canonical(NEW.request)
 OR NEW.request_hash IS DISTINCT FROM encode(public.digest(
   convert_to(NEW.request_canonical_text,'UTF8'),'sha256'),'hex')
 OR NEW.request IS DISTINCT FROM jsonb_build_object(
   'protocol_version',NEW.protocol_version,'simulation',true,'user_id',NEW.user_id,
   'source_id',NEW.source_id,'external_ref',NEW.external_ref,'kind',NEW.kind,
   'account_id',NEW.account_id,'amount_cents',NEW.amount_cents,'currency',NEW.currency,
   'counterparty_ref',NEW.counterparty_ref,'occurred_at',
   to_char(NEW.occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
 THEN RAISE EXCEPTION 'EXTERNAL_FACT_REQUEST_INVALID' USING ERRCODE='23514'; END IF;
 IF NEW.bank_status='SETTLED' AND (
  NEW.bank_result_canonical_text IS DISTINCT FROM public.audit_canonical(NEW.bank_result)
  OR NEW.bank_result_hash IS DISTINCT FROM encode(public.digest(
   convert_to(NEW.bank_result_canonical_text,'UTF8'),'sha256'),'hex')
  OR NEW.bank_result->>'protocol_version' IS DISTINCT FROM 'bank-external-settlement-v1'
  OR NEW.bank_result->'simulation' IS DISTINCT FROM 'true'::jsonb
  OR NEW.bank_result->>'user_id' IS DISTINCT FROM NEW.user_id::text
  OR NEW.bank_result->>'external_fact_id' IS DISTINCT FROM NEW.id::text
  OR NEW.bank_result->>'request_hash' IS DISTINCT FROM NEW.request_hash
  OR NEW.bank_result->>'settled_at' IS DISTINCT FROM
    to_char(NEW.settled_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
 THEN RAISE EXCEPTION 'EXTERNAL_FACT_RESULT_INVALID' USING ERRCODE='23514'; END IF;
 IF NEW.projection_status='PROJECTED' AND (
  NEW.projection_result_canonical_text IS DISTINCT FROM
 public.audit_canonical(NEW.projection_result)
  OR NEW.projection_result_hash IS DISTINCT FROM encode(public.digest(
   convert_to(NEW.projection_result_canonical_text,'UTF8'),'sha256'),'hex')
  OR NEW.projection_result->>'protocol_version' IS DISTINCT FROM 'bank-external-projection-v1'
  OR NEW.projection_result->'simulation' IS DISTINCT FROM 'true'::jsonb
  OR NEW.projection_result->>'user_id' IS DISTINCT FROM NEW.user_id::text
  OR NEW.projection_result->>'external_fact_id' IS DISTINCT FROM NEW.id::text
  OR NEW.projection_result->>'request_hash' IS DISTINCT FROM NEW.request_hash
  OR NEW.projection_result->>'bank_result_hash' IS DISTINCT FROM NEW.bank_result_hash
  OR NEW.projection_result->>'projected_at' IS DISTINCT FROM
    to_char(NEW.projected_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
 THEN RAISE EXCEPTION 'EXTERNAL_PROJECTION_RESULT_INVALID' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-mutable) IS DISTINCT FROM (to_jsonb(OLD)-mutable)
   OR NEW.updated_at<OLD.updated_at
   OR (OLD.bank_status IN ('SETTLED','REJECTED') AND EXISTS(
    SELECT 1 FROM unnest(bank_fields) k WHERE to_jsonb(NEW)->k IS DISTINCT FROM to_jsonb(OLD)->k))
   OR (NEW.bank_status='ACCEPTED' AND OLD.bank_status<>'ACCEPTED')
   OR (OLD.projection_status='PROJECTED' AND EXISTS(
    SELECT 1 FROM unnest(projection_fields) k WHERE to_jsonb(NEW)->k IS DISTINCT FROM
 to_jsonb(OLD)->k))
   OR (NEW.projection_status='PENDING' AND OLD.projection_status<>'PENDING')
  THEN RAISE EXCEPTION 'EXTERNAL_FACT_ORIGINAL_IMMUTABLE' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER external_bank_fact_immutable BEFORE INSERT OR UPDATE ON external_bank_facts
 FOR EACH ROW EXECUTE FUNCTION protect_external_bank_fact();

CREATE FUNCTION verify_external_bank_fact_postings() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE fid uuid; f public.external_bank_facts; p public.simulated_bank_postings;
 total integer; delta numeric; ids jsonb; economic jsonb; memo jsonb; memo_ids jsonb;
 expected bigint; predecessor public.simulated_bank_postings;
BEGIN
 IF TG_TABLE_NAME='external_bank_facts' THEN fid:=coalesce(NEW.id,OLD.id);
 ELSE fid:=coalesce(NEW.external_fact_id,OLD.external_fact_id); END IF;
 IF fid IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO f FROM public.external_bank_facts WHERE id=fid;
 IF f.id IS NULL THEN RETURN NULL; END IF;
 SELECT count(*),coalesce(sum(delta_cents),0),
  coalesce(jsonb_agg(to_jsonb(id::text) ORDER BY id),'[]'::jsonb),
  coalesce(jsonb_agg(to_jsonb(x)||jsonb_build_object(
   'created_at',to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'occurred_at',to_char(occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
   ORDER BY id),'[]'::jsonb)
 INTO total,delta,ids,economic FROM public.simulated_bank_postings x
 WHERE external_fact_id=fid AND ledger_dimension='ECONOMIC';
 IF f.bank_status<>'SETTLED' THEN
  IF total<>0 OR EXISTS(SELECT 1 FROM public.simulated_bank_postings WHERE external_fact_id=fid)
   THEN RAISE EXCEPTION 'EXTERNAL_UNSETTLED_POSTING' USING ERRCODE='23514'; END IF;
  RETURN NULL;
 END IF;
 IF total<>2 OR delta<>0
  OR jsonb_typeof(f.bank_result->'economic_posting_ids') IS DISTINCT FROM 'array'
  OR jsonb_array_length(f.bank_result->'economic_posting_ids')<>2
  OR ids IS DISTINCT FROM (SELECT jsonb_agg(v ORDER BY v#>>'{}') FROM
    jsonb_array_elements(f.bank_result->'economic_posting_ids') v)
  OR f.bank_result->>'posting_digest' IS DISTINCT FROM encode(public.digest(
   convert_to(public.audit_canonical(jsonb_build_object('postings',economic)),'UTF8'),'sha256'),
 'hex')
 THEN RAISE EXCEPTION 'EXTERNAL_ECONOMIC_POSTING_SET_INVALID' USING ERRCODE='23514'; END IF;
 expected:=CASE WHEN f.kind='INCOME' THEN f.amount_cents ELSE -f.amount_cents END;
 FOR p IN SELECT * FROM public.simulated_bank_postings WHERE external_fact_id=fid LOOP
  IF p.user_id<>f.user_id OR p.operation_id IS NOT NULL OR p.redemption_id IS NOT NULL
   OR (p.ledger_dimension='ECONOMIC' AND p.occurred_at<>f.occurred_at)
   OR (p.ledger_dimension<>'ECONOMIC' AND p.occurred_at<>f.projected_at)
  THEN RAISE EXCEPTION 'EXTERNAL_POSTING_ORIGIN_INVALID' USING ERRCODE='23514'; END IF;
  SELECT * INTO predecessor FROM public.simulated_bank_postings WHERE id=p.previous_posting_id;
  IF predecessor.id IS NULL OR predecessor.user_id<>p.user_id
   OR predecessor.ledger_key<>p.ledger_key OR predecessor.ledger_dimension<>p.ledger_dimension
   OR predecessor.ledger_metadata IS DISTINCT FROM p.ledger_metadata
   OR predecessor.sequence_number+1<>p.sequence_number
   OR predecessor.balance_after_cents<>p.balance_before_cents
   OR predecessor.occurred_at>p.occurred_at
  THEN RAISE EXCEPTION 'EXTERNAL_POSTING_PREDECESSOR_INVALID' USING ERRCODE='23514'; END IF;
  IF p.ledger_dimension='ECONOMIC' THEN
   IF NOT ((p.account_id=f.account_id AND p.position_id IS NULL
     AND p.ledger_key='CASH:'||f.account_id::text
     AND p.leg_ref='cash:'||f.account_id::text AND p.delta_cents=expected)
    OR (p.account_id IS NULL AND p.position_id IS NULL
     AND p.ledger_key='CLEARING:'||f.source_id||':'||f.counterparty_ref
     AND p.leg_ref='external:clearing' AND p.delta_cents=-expected))
    OR p.entry_kind<>(CASE WHEN p.delta_cents>0 THEN 'CREDIT' ELSE 'DEBIT' END)
   THEN RAISE EXCEPTION 'EXTERNAL_POSTING_ECONOMICS_INVALID' USING ERRCODE='23514'; END IF;
  ELSIF p.ledger_dimension<>'INCOME_LOCATION' OR f.projection_status<>'PROJECTED' THEN
   RAISE EXCEPTION 'EXTERNAL_MEMO_POSTING_INVALID' USING ERRCODE='23514';
  END IF;
 END LOOP;
 SELECT coalesce(jsonb_agg(to_jsonb(id::text) ORDER BY id),'[]'::jsonb),
  coalesce(jsonb_agg(to_jsonb(x)||jsonb_build_object(
   'created_at',to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
   'occurred_at',to_char(occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
   ORDER BY id),'[]'::jsonb)
 INTO memo_ids,memo FROM public.simulated_bank_postings x
 WHERE external_fact_id=fid AND ledger_dimension<>'ECONOMIC';
 IF f.projection_status='PROJECTED' AND (
  memo_ids IS DISTINCT FROM (SELECT coalesce(jsonb_agg(v ORDER BY v#>>'{}'),'[]'::jsonb)
    FROM jsonb_array_elements(f.projection_result->'memo_posting_ids') v)
  OR f.projection_result->>'memo_posting_digest' IS DISTINCT FROM encode(public.digest(
   convert_to(public.audit_canonical(jsonb_build_object('postings',memo)),'UTF8'),'sha256'),'hex'))
 THEN RAISE EXCEPTION 'EXTERNAL_MEMO_POSTING_SET_INVALID' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER external_bank_fact_complete AFTER INSERT OR UPDATE ON external_bank_facts
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION verify_external_bank_fact_postings();
CREATE CONSTRAINT TRIGGER external_bank_posting_complete AFTER INSERT OR DELETE ON
 simulated_bank_postings
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION verify_external_bank_fact_postings();
"""

OLD_AUDIT_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION audit_subject_insert() RETURNS trigger LANGUAGE plpgsql
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
CREATE OR REPLACE FUNCTION audit_event_insert() RETURNS trigger LANGUAGE plpgsql
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
"""


def upgrade() -> None:
    op.execute(TABLE)
    op.execute(POSTING_DDL)
    op.execute(EXTERNAL_GUARDS)
    op.execute(AUDIT_COMPAT)


def downgrade() -> None:
    if op.get_context().as_sql:
        raise ValueError("Offline downgrade is disabled")
    database = op.get_bind().engine.url.database
    if database is None or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None:
        raise ValueError("Downgrade requires a generated bf_test_<32 hex> database")
    incompatible = (
        op.get_bind()
        .exec_driver_sql("""
        SELECT EXISTS(SELECT 1 FROM external_bank_facts)
          OR EXISTS(SELECT 1 FROM simulated_bank_postings
            WHERE external_fact_id IS NOT NULL OR ledger_key LIKE 'CLEARING:%%')
          OR EXISTS(SELECT 1 FROM audit_subject_snapshots
            WHERE kind='BANK_EXTERNAL_FACT' OR (kind='BANK_POSTING' AND snapshot_version=2))
          OR EXISTS(SELECT 1 FROM audit_events WHERE payload_version=2
            OR event_type IN ('EXTERNAL_BANK_FACT_SETTLED','EXTERNAL_BANK_FACT_PROJECTED'))
    """)
        .scalar()
    )
    if incompatible:
        raise ValueError("Live or retained external bank history cannot downgrade to 0006")
    op.execute(OLD_AUDIT_FUNCTIONS)
    op.execute("""
        DROP TRIGGER external_bank_posting_complete ON simulated_bank_postings;
        DROP TRIGGER external_bank_fact_complete ON external_bank_facts;
        DROP TRIGGER external_bank_fact_immutable ON external_bank_facts;
        DROP FUNCTION verify_external_bank_fact_postings();
        DROP FUNCTION protect_external_bank_fact();
        ALTER TABLE simulated_bank_postings
         DROP CONSTRAINT ck_simulated_bank_postings_entry,
         DROP CONSTRAINT ck_simulated_bank_postings_ledger_identity,
         DROP CONSTRAINT uq_bank_posting_external_leg,
         DROP CONSTRAINT fk_simulated_bank_postings_external_fact_id_external_bank_facts,
         DROP COLUMN external_fact_id;
        DROP TABLE external_bank_facts;
        ALTER TABLE simulated_bank_postings ADD CONSTRAINT ck_simulated_bank_postings_entry
         CHECK ((entry_kind='OPENING' AND redemption_id IS NULL AND operation_id IS NULL
           AND leg_ref IS NULL AND sequence_number=1 AND previous_posting_id IS NULL
           AND balance_before_cents=0 AND delta_cents>=0)
          OR (entry_kind<>'OPENING' AND operation_id IS NOT NULL AND leg_ref IS NOT NULL
           AND previous_posting_id IS NOT NULL AND sequence_number>1));
        ALTER TABLE simulated_bank_postings ADD CONSTRAINT
         ck_simulated_bank_postings_ledger_identity CHECK (
          (ledger_dimension='ECONOMIC' AND ((account_id IS NOT NULL AND position_id IS NULL
           AND ledger_key='CASH:'||account_id::text) OR (account_id IS NULL AND position_id IS
 NOT NULL
           AND ledger_key='POSITION:'||position_id::text) OR (account_id IS NULL AND position_id
 IS NULL
           AND (ledger_key LIKE 'PAYEE:%%' OR ledger_key LIKE 'FEE:%%' OR ledger_key LIKE
 'LOSS:%%'))))
          OR (ledger_dimension IN ('GOAL_OWNERSHIP','INCOME_LOCATION','LIABILITY') AND
 position_id IS NULL));
    """)
