-- DRAFT: not embedded or applied until the whole source is frozen and hashed.
-- +goose Up
CREATE TABLE template_inventory_contracts (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,
 protocol_version text NOT NULL CHECK(protocol_version='synthetic-seed-inventory-v1'),
 seed_protection_version bigint NOT NULL CHECK(seed_protection_version>0),seed_protection_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 65536),original_hash hash256 NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,user_id),FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,seed_protection_version) REFERENCES protection_snapshots(environment_id,user_id,version)
);
CREATE TABLE template_policy_previews (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,
 policy_id uuid NOT NULL,version_id uuid NOT NULL,base_version bigint NOT NULL CHECK(base_version>=0),
 template_name text NOT NULL,goal_id uuid,timezone text NOT NULL CHECK(timezone IN('UTC','Asia/Shanghai')),
 session_id uuid NOT NULL,configuration bytea NOT NULL CHECK(octet_length(configuration) BETWEEN 1 AND 65536),configuration_hash hash256 NOT NULL,
 facts_original bytea NOT NULL CHECK(octet_length(facts_original) BETWEEN 1 AND 4194304),facts_hash hash256 NOT NULL,source_hash hash256 NOT NULL,
 preview_original bytea NOT NULL CHECK(octet_length(preview_original) BETWEEN 1 AND 4194304),preview_hash hash256 NOT NULL,
 review_bundle bytea NOT NULL CHECK(octet_length(review_bundle) BETWEEN 1 AND 8388608),reviewed_bundle_hash hash256 NOT NULL,
 created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>created_at),
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,version_id),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id)
);
CREATE TABLE template_policies (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,
 template_name text NOT NULL,goal_id uuid,current_version bigint NOT NULL CHECK(current_version>0),created_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id),
 CHECK((template_name='LongTermGoalPolicy')=(goal_id IS NOT NULL))
);
CREATE UNIQUE INDEX template_one_goal_identity ON template_policies(environment_id,user_id,goal_id) WHERE goal_id IS NOT NULL;
CREATE TABLE template_policy_confirmations (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,policy_id uuid NOT NULL,version_id uuid NOT NULL,
 preview_id uuid NOT NULL,session_id uuid NOT NULL,idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 16 AND 128),
 request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 65536),request_hash hash256 NOT NULL,
 reviewed_bundle_hash hash256 NOT NULL,configuration_hash hash256 NOT NULL,confirmed_at timestamptz NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 65536),original_hash hash256 NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,idempotency_key),UNIQUE(environment_id,user_id,version_id),UNIQUE(environment_id,user_id,preview_id),
 FOREIGN KEY(environment_id,user_id,preview_id) REFERENCES template_policy_previews(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id)
);
CREATE TABLE template_policy_versions (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,policy_id uuid NOT NULL,version bigint NOT NULL CHECK(version>0),id uuid NOT NULL,
 state text NOT NULL CHECK(state IN('ACTIVE','SUSPENDED','EXPIRED','REVOKED')),status_sequence bigint NOT NULL DEFAULT 1 CHECK(status_sequence>0),status_event_id uuid NOT NULL,
 confirmation_id uuid NOT NULL,timezone text NOT NULL CHECK(timezone IN('UTC','Asia/Shanghai')),
 configuration bytea NOT NULL CHECK(octet_length(configuration) BETWEEN 1 AND 65536),configuration_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072),original_hash hash256 NOT NULL,confirmed_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,policy_id,version),UNIQUE(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,policy_id) REFERENCES template_policies(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,confirmation_id) REFERENCES template_policy_confirmations(environment_id,user_id,id)
);
ALTER TABLE template_policies ADD CONSTRAINT template_current_version_owned FOREIGN KEY(environment_id,user_id,id,current_version) REFERENCES template_policy_versions(environment_id,user_id,policy_id,version) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE template_policy_confirmations ADD CONSTRAINT template_confirmation_version_owned FOREIGN KEY(environment_id,user_id,version_id) REFERENCES template_policy_versions(environment_id,user_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE template_policy_status_events (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,version_id uuid NOT NULL,sequence bigint NOT NULL CHECK(sequence>0),
 action text NOT NULL CHECK(action IN('CONFIRM','PAUSE','RESUME','REVOKE','EXPIRE')),
 old_state text,new_state text NOT NULL CHECK(new_state IN('ACTIVE','SUSPENDED','EXPIRED','REVOKED')),
 actor_kind text NOT NULL CHECK(actor_kind IN('USER','SYSTEM')),session_id uuid,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 65536),original_hash hash256 NOT NULL,occurred_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,version_id,sequence),
 FOREIGN KEY(environment_id,user_id,version_id) REFERENCES template_policy_versions(environment_id,user_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id),
 CHECK((actor_kind='USER')=(session_id IS NOT NULL)),CHECK(actor_kind='USER' OR action='EXPIRE'),
 CHECK((sequence=1)=(action='CONFIRM')),CHECK((old_state IS NULL)=(action='CONFIRM'))
);
ALTER TABLE template_policy_versions ADD CONSTRAINT template_status_event_owned FOREIGN KEY(environment_id,user_id,status_event_id) REFERENCES template_policy_status_events(environment_id,user_id,id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE template_obligations (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id text NOT NULL CHECK(length(id) BETWEEN 1 AND 160),
 policy_id uuid NOT NULL,version_id uuid NOT NULL,period_key text NOT NULL,kind text NOT NULL,due_date date NOT NULL,amount_cents cents NOT NULL CHECK(amount_cents>0),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 65536),original_hash hash256 NOT NULL,adopted_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),FOREIGN KEY(environment_id,user_id,policy_id) REFERENCES template_policies(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,version_id) REFERENCES template_policy_versions(environment_id,user_id,id)
);
CREATE TABLE template_obligation_payments (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,obligation_id text NOT NULL,operation_id uuid NOT NULL,receipt_id uuid NOT NULL,
 amount_cents cents NOT NULL CHECK(amount_cents>0),original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072),original_hash hash256 NOT NULL,recorded_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,operation_id,obligation_id),
 FOREIGN KEY(environment_id,user_id,obligation_id) REFERENCES template_obligations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,receipt_id) REFERENCES receipts(environment_id,user_id,id)
);
CREATE TABLE template_protection_publications (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,source_version bigint NOT NULL CHECK(source_version>0),basis_hash hash256 NOT NULL,
 local_date date NOT NULL,timezone text NOT NULL CHECK(timezone IN('UTC','Asia/Shanghai')),
 input_original bytea NOT NULL CHECK(octet_length(input_original) BETWEEN 1 AND 8388608),input_hash hash256 NOT NULL,
 result_original bytea NOT NULL CHECK(octet_length(result_original) BETWEEN 1 AND 4194304),result_hash hash256 NOT NULL,
 protection_hash hash256 NOT NULL,created_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,source_version),UNIQUE(environment_id,user_id,basis_hash),
 FOREIGN KEY(environment_id,user_id,source_version) REFERENCES protection_snapshots(environment_id,user_id,version)
);

-- +goose StatementBegin
CREATE FUNCTION protect_template_policy_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR ROW(NEW.environment_id,NEW.user_id,NEW.id,NEW.template_name,NEW.goal_id,NEW.created_at) IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.id,OLD.template_name,OLD.goal_id,OLD.created_at) OR NEW.current_version<>OLD.current_version+1 THEN RAISE EXCEPTION 'immutable template identity or nonsequential version' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION protect_template_policy_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR ROW(NEW.environment_id,NEW.user_id,NEW.policy_id,NEW.version,NEW.id,NEW.confirmation_id,NEW.timezone,NEW.configuration,NEW.configuration_hash,NEW.original,NEW.original_hash,NEW.confirmed_at) IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.policy_id,OLD.version,OLD.id,OLD.confirmation_id,OLD.timezone,OLD.configuration,OLD.configuration_hash,OLD.original,OLD.original_hash,OLD.confirmed_at) THEN RAISE EXCEPTION 'immutable reviewed template version' USING ERRCODE='23514';END IF;
 IF NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW;END IF;
 IF OLD.state IN('REVOKED','EXPIRED') OR NEW.status_sequence<>OLD.status_sequence+1 OR NEW.status_event_id=OLD.status_event_id OR NEW.state=OLD.state THEN RAISE EXCEPTION 'invalid template lifecycle transition' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION check_template_version_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c record;p record;e record;i record;
BEGIN
 SELECT * INTO c FROM template_policy_confirmations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.confirmation_id;
 SELECT * INTO p FROM template_policy_previews WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=c.preview_id;
 SELECT * INTO e FROM template_policy_status_events WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.status_event_id;
 SELECT * INTO i FROM template_policies WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.policy_id;
 IF c.id IS NULL OR p.id IS NULL OR e.id IS NULL OR i.id IS NULL OR c.version_id<>NEW.id OR c.policy_id<>NEW.policy_id OR p.policy_id<>NEW.policy_id OR p.version_id<>NEW.id OR p.base_version+1<>NEW.version OR c.configuration_hash<>NEW.configuration_hash OR p.configuration_hash<>NEW.configuration_hash OR p.configuration<>NEW.configuration OR c.reviewed_bundle_hash<>p.reviewed_bundle_hash OR c.confirmed_at<>NEW.confirmed_at OR c.confirmed_at<p.created_at OR c.confirmed_at>=p.expires_at OR p.template_name<>i.template_name OR p.goal_id IS DISTINCT FROM i.goal_id OR p.timezone<>NEW.timezone OR e.version_id<>NEW.id OR e.sequence<>NEW.status_sequence OR e.new_state<>NEW.state THEN RAISE EXCEPTION 'template version must bind its exact USER reviewed bundle and lifecycle event' USING ERRCODE='23514';END IF;
 IF TG_OP='INSERT' AND (e.action<>'CONFIRM' OR e.sequence<>1 OR e.old_state IS NOT NULL OR NEW.state<>'ACTIVE') THEN RAISE EXCEPTION 'new template version must bind initial USER confirmation' USING ERRCODE='23514';END IF;
 IF TG_OP='UPDATE' AND NEW.state IS DISTINCT FROM OLD.state AND (e.old_state IS DISTINCT FROM OLD.state OR NOT ((e.action='PAUSE' AND OLD.state='ACTIVE' AND NEW.state='SUSPENDED') OR (e.action='RESUME' AND OLD.state='SUSPENDED' AND NEW.state='ACTIVE') OR (e.action='REVOKE' AND OLD.state IN('ACTIVE','SUSPENDED') AND NEW.state='REVOKED') OR (e.action='EXPIRE' AND OLD.state IN('ACTIVE','SUSPENDED') AND NEW.state='EXPIRED'))) THEN RAISE EXCEPTION 'template lifecycle event does not match exact transition' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION check_template_payment_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE total numeric;bound cents;settled boolean;
BEGIN
 SELECT amount_cents INTO bound FROM template_obligations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.obligation_id FOR UPDATE;
 SELECT sum(amount_cents) INTO total FROM template_obligation_payments WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND obligation_id=NEW.obligation_id;
 SELECT o.state='SETTLED' AND o.receipt_id=NEW.receipt_id AND r.operation_id=o.id AND convert_from(o.bank_request,'UTF8')::jsonb->>'kind'='SPEND' INTO settled FROM operations o JOIN receipts r ON(r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.id=NEW.receipt_id) WHERE o.environment_id=NEW.environment_id AND o.user_id=NEW.user_id AND o.id=NEW.operation_id;
 IF total>bound OR settled IS DISTINCT FROM true THEN RAISE EXCEPTION 'template obligation payment must bind settled original receipt and cannot overpay' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
-- +goose StatementEnd
CREATE TRIGGER immutable_template_inventory BEFORE UPDATE OR DELETE ON template_inventory_contracts FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_template_preview BEFORE UPDATE OR DELETE ON template_policy_previews FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_template_confirmation BEFORE UPDATE OR DELETE ON template_policy_confirmations FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_template_identity BEFORE UPDATE OR DELETE ON template_policies FOR EACH ROW EXECUTE FUNCTION protect_template_policy_identity();
CREATE TRIGGER immutable_template_version BEFORE UPDATE OR DELETE ON template_policy_versions FOR EACH ROW EXECUTE FUNCTION protect_template_policy_version();
CREATE TRIGGER immutable_template_status_event BEFORE UPDATE OR DELETE ON template_policy_status_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE CONSTRAINT TRIGGER bound_template_version AFTER INSERT OR UPDATE ON template_policy_versions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_template_version_binding();
CREATE TRIGGER immutable_template_obligation BEFORE UPDATE OR DELETE ON template_obligations FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_template_obligation_payment BEFORE UPDATE OR DELETE ON template_obligation_payments FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE CONSTRAINT TRIGGER bound_template_payment AFTER INSERT ON template_obligation_payments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_template_payment_binding();
CREATE TRIGGER immutable_template_publication BEFORE UPDATE OR DELETE ON template_protection_publications FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();

-- +goose Down
DROP TABLE template_protection_publications,template_obligation_payments,template_obligations;
ALTER TABLE template_policy_versions DROP CONSTRAINT template_status_event_owned;
DROP TABLE template_policy_status_events;
ALTER TABLE template_policies DROP CONSTRAINT template_current_version_owned;
ALTER TABLE template_policy_confirmations DROP CONSTRAINT template_confirmation_version_owned;
DROP TABLE template_policy_versions,template_policy_confirmations,template_policies,template_policy_previews,template_inventory_contracts;
DROP FUNCTION check_template_payment_binding(),check_template_version_binding(),protect_template_policy_version(),protect_template_policy_identity();
