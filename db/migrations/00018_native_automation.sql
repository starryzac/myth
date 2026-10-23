-- DRAFT: standalone owner verification only; not embedded or applied to current.
-- +goose Up
CREATE TABLE native_grant_previews (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,session_id uuid NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 262144),original_hash hash256 NOT NULL,reviewed_hash hash256 NOT NULL,
 created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>created_at AND expires_at<=created_at+interval '2 minutes'),
 PRIMARY KEY(environment_id,user_id,id),FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id)
);
CREATE TABLE native_grants (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,version bigint NOT NULL CHECK(version=1),
 family text NOT NULL CHECK(family IN ('SINGLE_GOAL','JOINT_GOALS','ASSET_ALLOCATION','MATURITY_REINVEST','PERIODIC_PAYMENT')),
 preview_id uuid NOT NULL,session_id uuid NOT NULL,original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 524288),original_hash hash256 NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 256),request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 4096),request_hash hash256 NOT NULL,
 effective_from timestamptz NOT NULL,expires_at timestamptz NOT NULL,confirmed_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id,version),UNIQUE(environment_id,user_id,id),UNIQUE(environment_id,user_id,idempotency_key),
 FOREIGN KEY(environment_id,user_id,preview_id) REFERENCES native_grant_previews(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id),
 CHECK(effective_from=confirmed_at AND expires_at>effective_from AND expires_at<=effective_from+interval '366 days')
);
CREATE TABLE native_grant_state_events (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,grant_id uuid NOT NULL,grant_version bigint NOT NULL,
 sequence bigint NOT NULL CHECK(sequence>0),session_id uuid NOT NULL,action text NOT NULL CHECK(action IN ('CONFIRM','PAUSE','RESUME','REVOKE')),
 old_state text NOT NULL,new_state text NOT NULL CHECK(new_state IN ('ACTIVE','PAUSED','REVOKED')),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 8192),original_hash hash256 NOT NULL,created_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,grant_id,grant_version,sequence),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES native_grants(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id)
);
CREATE TABLE native_grant_states (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,grant_id uuid NOT NULL,grant_version bigint NOT NULL,
 state text NOT NULL CHECK(state IN ('ACTIVE','PAUSED','REVOKED')),sequence bigint NOT NULL CHECK(sequence>0),event_id uuid NOT NULL,updated_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,grant_id,grant_version),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES native_grants(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,event_id) REFERENCES native_grant_state_events(environment_id,user_id,id)
);
CREATE TABLE native_occurrences (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,grant_id uuid NOT NULL,grant_version bigint NOT NULL,
 family text NOT NULL CHECK(family IN ('SINGLE_GOAL','JOINT_GOALS','ASSET_ALLOCATION','MATURITY_REINVEST','PERIODIC_PAYMENT')),
 occurrence_key hash256 NOT NULL,source_kind text NOT NULL CHECK(source_kind IN ('INCOME_EVENT','PRINCIPAL_RETURN','PAYMENT_OCCURRENCE')),
 source_id text NOT NULL CHECK(length(source_id) BETWEEN 1 AND 128),source_hash hash256 NOT NULL,event_id uuid,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 65536),original_hash hash256 NOT NULL,
 source_original bytea NOT NULL CHECK(octet_length(source_original) BETWEEN 1 AND 1048576),source_original_hash hash256 NOT NULL,
 state text NOT NULL DEFAULT 'READY' CHECK(state IN ('READY','RUNNING','ACCEPTED','NO_ACTION','BLOCKED','EXHAUSTED')),
 claim_generation bigint NOT NULL DEFAULT 0 CHECK(claim_generation>=0),lease_owner text,lease_until timestamptz,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),max_attempts integer NOT NULL DEFAULT 8 CHECK(max_attempts BETWEEN 1 AND 8),
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),last_error text NOT NULL DEFAULT '',created_at timestamptz NOT NULL,finished_at timestamptz,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,occurrence_key),
 UNIQUE(environment_id,user_id,grant_id,grant_version,family,source_kind,source_id),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES native_grants(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,event_id) REFERENCES outbox(environment_id,user_id,id),
 CHECK((lease_owner IS NULL)=(lease_until IS NULL)),CHECK((state='RUNNING')=(lease_owner IS NOT NULL)),
 CHECK((source_kind='INCOME_EVENT')=(event_id IS NOT NULL))
);
CREATE INDEX native_occurrence_ready ON native_occurrences(environment_id,family,next_attempt_at,id) WHERE state IN ('READY','RUNNING');
CREATE TABLE native_automation_event_receipts (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,grant_id uuid NOT NULL,grant_version bigint NOT NULL,event_id uuid NOT NULL,
 outcome text NOT NULL CHECK(outcome IN ('REGISTERED','INVALID','UNSUPPORTED','NOT_ELIGIBLE')),reason text NOT NULL DEFAULT '',
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 1048576),original_hash hash256 NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,user_id,grant_id,grant_version,event_id),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES native_grants(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,event_id) REFERENCES outbox(environment_id,user_id,id),
 CHECK(encode(sha256(original),'hex')=original_hash)
);
CREATE TABLE native_grant_budget_claims (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,occurrence_id uuid NOT NULL,grant_id uuid NOT NULL,grant_version bigint NOT NULL,
 period text NOT NULL CHECK(period~'^[0-9]{4}-(0[1-9]|1[0-2])$'),amount_cents cents NOT NULL CHECK(amount_cents>0),
 state text NOT NULL DEFAULT 'RESERVED' CHECK(state IN ('RESERVED','FINAL')),spent_cents cents NOT NULL DEFAULT 0 CHECK(spent_cents<=amount_cents),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 1048576),original_hash hash256 NOT NULL,
 created_at timestamptz NOT NULL,closed_at timestamptz,accepted_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(environment_id,user_id,occurrence_id),FOREIGN KEY(environment_id,user_id,occurrence_id) REFERENCES native_occurrences(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES native_grants(environment_id,user_id,id,version),
 CHECK((state='FINAL')=(closed_at IS NOT NULL)),CHECK(state<>'RESERVED' OR spent_cents=0)
);
CREATE TABLE native_standing_plans (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,occurrence_id uuid NOT NULL,grant_id uuid NOT NULL,grant_version bigint NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 8388608),original_hash hash256 NOT NULL,created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,occurrence_id) REFERENCES native_occurrences(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES native_grants(environment_id,user_id,id,version),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '2 minutes'),CHECK(encode(sha256(original),'hex')=original_hash)
);
CREATE INDEX native_grant_budget_scope ON native_grant_budget_claims(environment_id,user_id,grant_id,grant_version,period);
CREATE TABLE native_occurrence_operations (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,occurrence_id uuid NOT NULL,operation_id uuid NOT NULL,
 economic_amount_cents cents NOT NULL,effect_hash hash256 NOT NULL,bank_request_hash hash256 NOT NULL,created_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,occurrence_id) REFERENCES native_grant_budget_claims(environment_id,user_id,occurrence_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id)
);
-- Operational intake schedule only. Financial credit originals stay in the
-- bank/source evidence and income/outbox tables; this carries no credential.
CREATE TABLE native_income_intakes (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'READY' CHECK(state IN ('READY','RUNNING','EXHAUSTED')),
 claim_generation bigint NOT NULL DEFAULT 0 CHECK(claim_generation>=0),lease_owner text,lease_until timestamptz,
 failures integer NOT NULL DEFAULT 0 CHECK(failures BETWEEN 0 AND 8),next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),last_error text NOT NULL DEFAULT '',
 credit_watermark bigint NOT NULL DEFAULT 0 CHECK(credit_watermark>=0),v1_watermark bigint NOT NULL DEFAULT 0 CHECK(v1_watermark>=0),v2_watermark bigint NOT NULL DEFAULT 0 CHECK(v2_watermark>=0),
 capture_hash hash256,last_observed_at timestamptz,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,user_id),FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 CHECK((lease_owner IS NULL)=(lease_until IS NULL)),CHECK((state='RUNNING')=(lease_owner IS NOT NULL)),CHECK(state<>'EXHAUSTED' OR failures=8)
);
CREATE INDEX native_income_intake_due ON native_income_intakes(environment_id,next_attempt_at,user_id) WHERE state IN ('READY','RUNNING');

-- +goose StatementBegin
CREATE FUNCTION native_automation_original_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;p jsonb;st native_grant_states;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash THEN RAISE EXCEPTION 'native automation original hash differs' USING ERRCODE='23514';END IF;
 raw:=convert_from(NEW.original,'UTF8')::jsonb;
 IF raw->'scope' IS DISTINCT FROM jsonb_build_object('environment_id',NEW.environment_id::text,'user_id',NEW.user_id::text) OR raw->>'id' IS DISTINCT FROM NEW.id::text THEN RAISE EXCEPTION 'native automation original scope differs' USING ERRCODE='23514';END IF;
 IF TG_TABLE_NAME='native_grant_previews' THEN
  IF raw->>'protocol' IS DISTINCT FROM 'native-automation-v1/grant-preview' OR raw->>'session_id' IS DISTINCT FROM NEW.session_id::text OR raw->>'reviewed_hash' IS DISTINCT FROM NEW.reviewed_hash::text OR (raw->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at OR (raw->>'expires_at')::timestamptz IS DISTINCT FROM NEW.expires_at OR raw#>'{terms,scope}' IS DISTINCT FROM raw->'scope' THEN RAISE EXCEPTION 'native grant preview columns differ' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='native_grants' THEN
  SELECT convert_from(original,'UTF8')::jsonb INTO p FROM native_grant_previews WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.preview_id;
  IF encode(sha256(NEW.request),'hex')<>NEW.request_hash OR raw->>'protocol' IS DISTINCT FROM 'native-automation-v1/grant' OR raw->>'principal_kind' IS DISTINCT FROM 'USER' OR raw->>'version' IS DISTINCT FROM '1' OR raw->>'session_id' IS DISTINCT FROM NEW.session_id::text OR raw->>'preview_id' IS DISTINCT FROM NEW.preview_id::text OR raw->>'reviewed_hash' IS DISTINCT FROM p->>'reviewed_hash' OR raw->'reviewed_terms' IS DISTINCT FROM p->'terms' OR ((raw->'terms')-'effective_from') IS DISTINCT FROM ((p->'terms')-'effective_from') OR raw#>>'{terms,family}' IS DISTINCT FROM NEW.family OR (raw->>'confirmed_at')::timestamptz IS DISTINCT FROM NEW.confirmed_at OR (raw#>>'{terms,effective_from}')::timestamptz IS DISTINCT FROM NEW.effective_from OR (raw#>>'{terms,expires_at}')::timestamptz IS DISTINCT FROM NEW.expires_at OR NEW.confirmed_at<(p->>'created_at')::timestamptz OR NEW.confirmed_at>=(p->>'expires_at')::timestamptz THEN RAISE EXCEPTION 'native grant does not bind real reviewed terms' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='native_grant_state_events' THEN
  IF raw->>'protocol' IS DISTINCT FROM 'native-automation-v1/grant-state' OR raw->>'principal_kind' IS DISTINCT FROM 'USER' OR raw->>'session_id' IS DISTINCT FROM NEW.session_id::text OR raw->>'grant_id' IS DISTINCT FROM NEW.grant_id::text OR (raw->>'grant_version')::bigint IS DISTINCT FROM NEW.grant_version OR (raw->>'sequence')::bigint IS DISTINCT FROM NEW.sequence OR raw->>'action' IS DISTINCT FROM NEW.action OR raw->>'old_state' IS DISTINCT FROM NEW.old_state OR raw->>'new_state' IS DISTINCT FROM NEW.new_state OR (raw->>'at')::timestamptz IS DISTINCT FROM NEW.created_at THEN RAISE EXCEPTION 'native grant lifecycle original differs' USING ERRCODE='23514';END IF;
  SELECT * INTO st FROM native_grant_states WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND grant_id=NEW.grant_id AND grant_version=NEW.grant_version FOR UPDATE;
  IF NEW.sequence=1 THEN
   IF st.grant_id IS NOT NULL OR NEW.action<>'CONFIRM' OR NEW.old_state<>'' OR NEW.new_state<>'ACTIVE' THEN RAISE EXCEPTION 'native grant initial state invalid' USING ERRCODE='23514';END IF;
  ELSIF st.grant_id IS NULL OR NEW.sequence<>st.sequence+1 OR NEW.old_state<>st.state OR NOT(NEW.action='PAUSE' AND st.state='ACTIVE' AND NEW.new_state='PAUSED' OR NEW.action='RESUME' AND st.state='PAUSED' AND NEW.new_state='ACTIVE' OR NEW.action='REVOKE' AND st.state IN ('ACTIVE','PAUSED') AND NEW.new_state='REVOKED') THEN RAISE EXCEPTION 'native grant state transition invalid' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='native_occurrences' THEN
  IF raw->>'protocol' IS DISTINCT FROM 'native-automation-v1/occurrence' OR raw->>'grant_id' IS DISTINCT FROM NEW.grant_id::text OR (raw->>'grant_version')::bigint IS DISTINCT FROM NEW.grant_version OR raw->>'family' IS DISTINCT FROM NEW.family OR raw->>'key' IS DISTINCT FROM NEW.occurrence_key::text OR raw#>>'{source,kind}' IS DISTINCT FROM NEW.source_kind OR raw#>>'{source,id}' IS DISTINCT FROM NEW.source_id OR raw#>>'{source,original_hash}' IS DISTINCT FROM NEW.source_hash::text OR (raw->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at OR encode(sha256(NEW.source_original),'hex')<>NEW.source_original_hash THEN RAISE EXCEPTION 'native occurrence original differs' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_grant_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e native_grant_state_events;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'native grant state cannot be deleted' USING ERRCODE='23514';END IF;
 SELECT * INTO e FROM native_grant_state_events WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.event_id;
 IF e.id IS NULL OR e.grant_id<>NEW.grant_id OR e.grant_version<>NEW.grant_version OR e.sequence<>NEW.sequence OR e.new_state<>NEW.state OR e.created_at<>NEW.updated_at THEN RAISE EXCEPTION 'native grant state missing immutable USER event' USING ERRCODE='23514';END IF;
 IF TG_OP='UPDATE' AND (ROW(NEW.environment_id,NEW.user_id,NEW.grant_id,NEW.grant_version) IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.grant_id,OLD.grant_version) OR NEW.sequence<>OLD.sequence+1 OR e.old_state<>OLD.state) THEN RAISE EXCEPTION 'native grant state identity or sequence differs' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_occurrence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'state'-'claim_generation'-'lease_owner'-'lease_until'-'attempts'-'next_attempt_at'-'last_error'-'finished_at') IS DISTINCT FROM (to_jsonb(OLD)-'state'-'claim_generation'-'lease_owner'-'lease_until'-'attempts'-'next_attempt_at'-'last_error'-'finished_at') THEN RAISE EXCEPTION 'immutable native occurrence identity' USING ERRCODE='23514';END IF;
 IF NEW.claim_generation<OLD.claim_generation OR NEW.attempts<OLD.attempts THEN RAISE EXCEPTION 'native occurrence generations cannot reverse' USING ERRCODE='23514';END IF;
 IF OLD.state IN ('ACCEPTED','NO_ACTION','BLOCKED','EXHAUSTED') AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN RAISE EXCEPTION 'native occurrence outcome is terminal' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_budget_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g native_grants;o native_occurrences;spent numeric;pending boolean;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'native budget original cannot be deleted' USING ERRCODE='23514';END IF;
 IF TG_OP='INSERT' THEN
  SELECT * INTO g FROM native_grants WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.grant_id AND version=NEW.grant_version;
  PERFORM 1 FROM native_grant_states WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND grant_id=NEW.grant_id AND grant_version=NEW.grant_version AND state='ACTIVE' FOR UPDATE;
  IF NOT FOUND OR g.id IS NULL OR encode(sha256(NEW.original),'hex')<>NEW.original_hash OR NEW.state<>'RESERVED' OR NEW.accepted_xid IS DISTINCT FROM pg_current_xact_id() OR NEW.created_at<g.effective_from OR NEW.created_at>=g.expires_at OR NEW.period IS DISTINCT FROM to_char(NEW.created_at AT TIME ZONE (convert_from(g.original,'UTF8')::jsonb#>>'{terms,timezone}'),'YYYY-MM') THEN RAISE EXCEPTION 'native budget requires original active grant and exact period' USING ERRCODE='23514';END IF;
  SELECT * INTO o FROM native_occurrences WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.occurrence_id;
  IF o.grant_id IS DISTINCT FROM NEW.grant_id OR o.grant_version IS DISTINCT FROM NEW.grant_version OR o.family IS DISTINCT FROM g.family THEN RAISE EXCEPTION 'native budget occurrence grant differs' USING ERRCODE='23514';END IF;
 ELSE
  IF (to_jsonb(NEW)-'state'-'spent_cents'-'closed_at') IS DISTINCT FROM (to_jsonb(OLD)-'state'-'spent_cents'-'closed_at') OR OLD.state<>'RESERVED' OR NEW.state<>'FINAL' THEN RAISE EXCEPTION 'native budget cannot be replaced or reopened' USING ERRCODE='23514';END IF;
  SELECT coalesce(sum(b.economic_amount_cents) FILTER(WHERE p.status='SETTLED'),0),coalesce(bool_or(p.status NOT IN ('SETTLED','REJECTED','CANCELLED')),true) INTO spent,pending FROM native_occurrence_operations b JOIN native_operations p ON(p.environment_id=b.environment_id AND p.user_id=b.user_id AND p.id=b.operation_id) WHERE b.environment_id=NEW.environment_id AND b.user_id=NEW.user_id AND b.occurrence_id=NEW.occurrence_id;
  IF pending OR spent<>NEW.spent_cents THEN RAISE EXCEPTION 'native budget cannot release UNKNOWN or omit actual settled effects' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_budget_final_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g native_grants;life numeric;month_used numeric;
BEGIN
 SELECT * INTO g FROM native_grants WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.grant_id AND version=NEW.grant_version;
 SELECT sum(CASE WHEN state='RESERVED' THEN amount_cents ELSE spent_cents END),coalesce(sum(CASE WHEN state='RESERVED' THEN amount_cents ELSE spent_cents END) FILTER(WHERE period=NEW.period),0) INTO life,month_used FROM native_grant_budget_claims WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND grant_id=NEW.grant_id AND grant_version=NEW.grant_version;
 IF NEW.amount_cents>(convert_from(g.original,'UTF8')::jsonb#>>'{terms,single_cap_cents}')::numeric OR life>(convert_from(g.original,'UTF8')::jsonb#>>'{terms,lifetime_cap_cents}')::numeric OR month_used>(convert_from(g.original,'UTF8')::jsonb#>>'{terms,monthly_cap_cents}')::numeric THEN RAISE EXCEPTION 'native pending and settled budgets exceed original caps' USING ERRCODE='23514';END IF;
 IF NOT EXISTS(SELECT 1 FROM native_occurrence_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND occurrence_id=NEW.occurrence_id) THEN RAISE EXCEPTION 'native budget missing same T1 operation binding' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_occurrence_operation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o native_operations;b native_grant_budget_claims;used numeric;
BEGIN
 SELECT * INTO o FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.operation_id;
 SELECT * INTO b FROM native_grant_budget_claims WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND occurrence_id=NEW.occurrence_id FOR UPDATE;
 IF o.mode IS DISTINCT FROM 'STANDING' OR o.grant_id IS DISTINCT FROM b.grant_id OR o.grant_version IS DISTINCT FROM b.grant_version OR o.accepted_xid IS DISTINCT FROM pg_current_xact_id() OR b.state IS DISTINCT FROM 'RESERVED' OR o.effect_hash IS DISTINCT FROM NEW.effect_hash OR o.bank_request_hash IS DISTINCT FROM NEW.bank_request_hash THEN RAISE EXCEPTION 'native standing operation missing exact same T1 authority' USING ERRCODE='23514';END IF;
 SELECT coalesce(sum(economic_amount_cents),0) INTO used FROM native_occurrence_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND occurrence_id=NEW.occurrence_id;
 IF used+NEW.economic_amount_cents>b.amount_cents THEN RAISE EXCEPTION 'native economic bindings exceed held grant budget' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_standing_operation_check() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.mode='STANDING' AND NOT EXISTS(SELECT 1 FROM native_occurrence_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND operation_id=NEW.id) THEN RAISE EXCEPTION 'native standing operation lacks persistent occurrence budget binding' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_income_intake_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'native income observation identity retained' USING ERRCODE='23514';END IF;
 IF ROW(NEW.environment_id,NEW.user_id,NEW.created_at) IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.created_at) OR NEW.credit_watermark<OLD.credit_watermark OR NEW.v1_watermark<OLD.v1_watermark OR NEW.v2_watermark<OLD.v2_watermark OR NEW.claim_generation<OLD.claim_generation OR NEW.claim_generation>OLD.claim_generation+1 THEN RAISE EXCEPTION 'native income observation identity or watermark moves backwards' USING ERRCODE='23514';END IF;
 IF NEW.claim_generation<>OLD.claim_generation AND NEW.state<>'RUNNING' OR NEW.state='RUNNING' AND (NEW.claim_generation<>OLD.claim_generation+1 OR NEW.lease_until<=clock_timestamp()) THEN RAISE EXCEPTION 'native income observation lease requires new generation' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER native_grant_preview_original BEFORE INSERT ON native_grant_previews FOR EACH ROW EXECUTE FUNCTION native_automation_original_guard();
CREATE TRIGGER native_grant_original BEFORE INSERT ON native_grants FOR EACH ROW EXECUTE FUNCTION native_automation_original_guard();
CREATE TRIGGER native_grant_event_original BEFORE INSERT ON native_grant_state_events FOR EACH ROW EXECUTE FUNCTION native_automation_original_guard();
CREATE TRIGGER native_occurrence_original BEFORE INSERT ON native_occurrences FOR EACH ROW EXECUTE FUNCTION native_automation_original_guard();
CREATE TRIGGER native_grant_preview_immutable BEFORE UPDATE OR DELETE ON native_grant_previews FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_grant_immutable BEFORE UPDATE OR DELETE ON native_grants FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_grant_event_immutable BEFORE UPDATE OR DELETE ON native_grant_state_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_grant_current_state BEFORE INSERT OR UPDATE OR DELETE ON native_grant_states FOR EACH ROW EXECUTE FUNCTION native_grant_state_guard();
CREATE TRIGGER native_occurrence_identity BEFORE UPDATE OR DELETE ON native_occurrences FOR EACH ROW EXECUTE FUNCTION native_occurrence_guard();
CREATE TRIGGER native_automation_event_immutable BEFORE UPDATE OR DELETE ON native_automation_event_receipts FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_standing_plan_immutable BEFORE UPDATE OR DELETE ON native_standing_plans FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_grant_budget_original BEFORE INSERT OR UPDATE OR DELETE ON native_grant_budget_claims FOR EACH ROW EXECUTE FUNCTION native_budget_guard();
CREATE CONSTRAINT TRIGGER native_budget_bound AFTER INSERT OR UPDATE ON native_grant_budget_claims DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_budget_final_check();
CREATE TRIGGER native_occurrence_operation_original BEFORE INSERT ON native_occurrence_operations FOR EACH ROW EXECUTE FUNCTION native_occurrence_operation_guard();
CREATE TRIGGER native_occurrence_operation_immutable BEFORE UPDATE OR DELETE ON native_occurrence_operations FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE CONSTRAINT TRIGGER native_standing_bound AFTER INSERT ON native_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_standing_operation_check();
CREATE TRIGGER native_income_intake_identity BEFORE UPDATE OR DELETE ON native_income_intakes FOR EACH ROW EXECUTE FUNCTION native_income_intake_guard();
GRANT SELECT,INSERT ON native_grant_previews,native_grants,native_grant_state_events,native_occurrence_operations,native_automation_event_receipts,native_standing_plans TO zygo_app;
GRANT SELECT,INSERT,UPDATE ON native_grant_states,native_occurrences,native_grant_budget_claims TO zygo_app;
GRANT SELECT,INSERT,UPDATE ON native_income_intakes TO zygo_app;
-- Owner default privileges may already include UPDATE/DELETE. Explicitly
-- remove them from append-only sources; triggers remain an independent guard.
REVOKE UPDATE,DELETE ON native_grant_previews,native_grants,native_grant_state_events,native_occurrence_operations,native_automation_event_receipts,native_standing_plans,native_payment_sources,native_obligation_payments FROM zygo_app;
REVOKE DELETE ON native_grant_states,native_occurrences,native_grant_budget_claims FROM zygo_app;
REVOKE DELETE ON native_income_intakes FROM zygo_app;
-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'native standing authority and occurrence originals are forward only';END $$;
-- +goose StatementEnd
