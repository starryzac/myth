-- +goose Up
CREATE DOMAIN cents AS bigint CHECK (VALUE >= 0 AND VALUE <= 9000000000000000);
CREATE DOMAIN hash256 AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE environments (
 id uuid PRIMARY KEY, namespace text NOT NULL UNIQUE CHECK(length(namespace) BETWEEN 1 AND 128),
 writer_generation bigint NOT NULL DEFAULT 1 CHECK(writer_generation>0), control_version bigint NOT NULL DEFAULT 1 CHECK(control_version>0)
);
CREATE TABLE users (
 environment_id uuid NOT NULL REFERENCES environments(id), id uuid NOT NULL,
 funds_version bigint NOT NULL DEFAULT 1 CHECK(funds_version>0), source_version bigint NOT NULL DEFAULT 1 CHECK(source_version>0),
 PRIMARY KEY(environment_id,id)
);
CREATE TABLE accounts (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, name text NOT NULL DEFAULT '',
 currency text NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'), balance_cents cents NOT NULL DEFAULT 0,
 goal_owned_cents cents NOT NULL DEFAULT 0, observed_at timestamptz NOT NULL,
 CHECK(goal_owned_cents<=balance_cents), PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);
CREATE TABLE income_origins (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, original_account_id uuid NOT NULL,
 currency text NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'), amount_cents cents NOT NULL CHECK(amount_cents>0),
 occurred_at timestamptz NOT NULL, observed_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
 evidence_id uuid NOT NULL, evidence_hash hash256 NOT NULL,
 CHECK(occurred_at<=observed_at AND observed_at<=recorded_at), PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,original_account_id) REFERENCES accounts(environment_id,user_id,id)
);
CREATE TABLE income_locations (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, origin_id uuid NOT NULL, account_id uuid NOT NULL,
 spent_cents cents NOT NULL DEFAULT 0, assigned_cents cents NOT NULL DEFAULT 0, reserved_cents cents NOT NULL DEFAULT 0,
 legacy_reserved_cents cents NOT NULL DEFAULT 0, available_cents cents NOT NULL DEFAULT 0,
 CHECK(legacy_reserved_cents<=reserved_cents), PRIMARY KEY(environment_id,user_id,id),
 UNIQUE(environment_id,user_id,origin_id,account_id),
 FOREIGN KEY(environment_id,user_id,origin_id) REFERENCES income_origins(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,account_id) REFERENCES accounts(environment_id,user_id,id)
);
CREATE TABLE confirmations (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL,
 target_kind text NOT NULL CHECK(target_kind IN ('POLICY','GRANT','PLAN')), target_id uuid NOT NULL, target_version bigint NOT NULL CHECK(target_version>0),
 reviewed_hash hash256 NOT NULL, confirmed_at timestamptz NOT NULL, expires_at timestamptz, original bytea NOT NULL CHECK(octet_length(original)>0),
 CHECK(expires_at IS NULL OR expires_at>confirmed_at), PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);
CREATE TABLE goals (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, name text NOT NULL DEFAULT '', account_id uuid NOT NULL,
 policy_id uuid, policy_version bigint, target_cents cents NOT NULL CHECK(target_cents>0), assigned_cents cents NOT NULL DEFAULT 0,
 month_period text NOT NULL CHECK(month_period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'), month_contributed_cents cents NOT NULL DEFAULT 0,
 version bigint NOT NULL DEFAULT 1 CHECK(version>0), CHECK((policy_id IS NULL)=(policy_version IS NULL)), CHECK(assigned_cents<=target_cents),
 PRIMARY KEY(environment_id,user_id,id), FOREIGN KEY(environment_id,user_id,account_id) REFERENCES accounts(environment_id,user_id,id)
);
CREATE TABLE policies (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, version bigint NOT NULL CHECK(version>0),
 status text NOT NULL CHECK(status IN ('ACTIVE','PAUSED','REVOKED')), is_current boolean NOT NULL DEFAULT true,
 confirmation_id uuid NOT NULL, original bytea NOT NULL CHECK(octet_length(original)>0), PRIMARY KEY(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,confirmation_id) REFERENCES confirmations(environment_id,user_id,id)
);
CREATE UNIQUE INDEX one_current_policy ON policies(environment_id,user_id,id) WHERE is_current;
ALTER TABLE goals ADD CONSTRAINT goal_policy_fk FOREIGN KEY(environment_id,user_id,policy_id,policy_version) REFERENCES policies(environment_id,user_id,id,version) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE grants (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, version bigint NOT NULL CHECK(version>0),
 policy_id uuid NOT NULL, policy_version bigint NOT NULL, status text NOT NULL CHECK(status IN ('ACTIVE','PAUSED','REVOKED')),
 is_current boolean NOT NULL DEFAULT true, confirmation_id uuid NOT NULL, original bytea NOT NULL CHECK(octet_length(original)>0),
 PRIMARY KEY(environment_id,user_id,id,version), FOREIGN KEY(environment_id,user_id,policy_id,policy_version) REFERENCES policies(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,confirmation_id) REFERENCES confirmations(environment_id,user_id,id)
);
CREATE UNIQUE INDEX one_current_grant ON grants(environment_id,user_id,id) WHERE is_current;
CREATE TABLE plans (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL,
 policy_id uuid NOT NULL, policy_version bigint NOT NULL, grant_id uuid, grant_version bigint, goal_id uuid NOT NULL,
 candidate bytea NOT NULL CHECK(octet_length(candidate)>0), input bytea NOT NULL CHECK(octet_length(input)>0),
 effect_hash hash256 NOT NULL, input_hash hash256 NOT NULL, dependencies jsonb NOT NULL CHECK(jsonb_typeof(dependencies)='object'),
 created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL CHECK(expires_at>created_at), CHECK((grant_id IS NULL)=(grant_version IS NULL)),
 PRIMARY KEY(environment_id,user_id,id), FOREIGN KEY(environment_id,user_id,policy_id,policy_version) REFERENCES policies(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES grants(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id)
);
CREATE TABLE operations (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, plan_id uuid NOT NULL,
 policy_id uuid NOT NULL, policy_version bigint NOT NULL, grant_id uuid, grant_version bigint,
 command_kind text NOT NULL CHECK(length(command_kind) BETWEEN 1 AND 128), idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 256),
 request_hash hash256 NOT NULL, request bytea NOT NULL CHECK(octet_length(request)>0),
 bank_namespace text NOT NULL CHECK(length(bank_namespace) BETWEEN 1 AND 128), bank_key text NOT NULL CHECK(length(bank_key) BETWEEN 1 AND 256),
 bank_request_hash hash256 NOT NULL, bank_request bytea NOT NULL CHECK(octet_length(bank_request)>0),
 status text NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','SUBMITTING','PENDING_SETTLEMENT','UNKNOWN','RECONCILING','SETTLED','REJECTED','CANCELLED')), version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 permit_id uuid, permit_signature text, dependencies jsonb NOT NULL CHECK(jsonb_typeof(dependencies)='object'),
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, CHECK((grant_id IS NULL)=(grant_version IS NULL)),
 PRIMARY KEY(environment_id,user_id,id), UNIQUE(environment_id,user_id,command_kind,idempotency_key), UNIQUE(bank_namespace,bank_key),
 FOREIGN KEY(environment_id,user_id,plan_id) REFERENCES plans(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,policy_id,policy_version) REFERENCES policies(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES grants(environment_id,user_id,id,version)
);
CREATE TABLE income_reservations (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, action_id uuid NOT NULL,
 operation text NOT NULL CHECK(operation IN ('ALLOCATE_GOAL','TRANSFER_INTERNAL','SPEND')), destination_account_id uuid,
 state text NOT NULL CHECK(state IN ('RESERVED','COMMITTED','RELEASED')),
 CHECK((operation='TRANSFER_INTERNAL')=(destination_account_id IS NOT NULL)),
 PRIMARY KEY(environment_id,user_id,action_id),
 FOREIGN KEY(environment_id,user_id,action_id) REFERENCES operations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,destination_account_id) REFERENCES accounts(environment_id,user_id,id)
);
CREATE TABLE income_claims (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, action_id uuid NOT NULL, fragment_id uuid NOT NULL, amount_cents cents NOT NULL CHECK(amount_cents>0),
 PRIMARY KEY(environment_id,user_id,action_id,fragment_id),
 FOREIGN KEY(environment_id,user_id,action_id) REFERENCES income_reservations(environment_id,user_id,action_id),
 FOREIGN KEY(environment_id,user_id,fragment_id) REFERENCES income_locations(environment_id,user_id,id)
);
CREATE TABLE jobs (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, operation_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'READY', claim_generation bigint NOT NULL DEFAULT 0 CHECK(claim_generation>=0), lease_owner text, lease_until timestamptz,
 next_attempt_at timestamptz NOT NULL, attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), max_attempts integer NOT NULL DEFAULT 8 CHECK(max_attempts>0),
 paused boolean NOT NULL DEFAULT false, last_error text NOT NULL DEFAULT '', PRIMARY KEY(environment_id,user_id,id),
 UNIQUE(environment_id,user_id,operation_id), FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id)
);
CREATE INDEX ready_jobs ON jobs(next_attempt_at,id) WHERE state IN ('READY','RUNNING') AND NOT paused;
CREATE TABLE journals (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, operation_id uuid,
 kind text NOT NULL, status text NOT NULL CHECK(status IN ('DRAFT','POSTED')), created_at timestamptz NOT NULL, posted_at timestamptz,
 CHECK((status='POSTED')=(posted_at IS NOT NULL)), PRIMARY KEY(environment_id,user_id,id),
 UNIQUE(environment_id,user_id,operation_id,kind), FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id)
);
CREATE TABLE postings (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, journal_id uuid NOT NULL, leg_ref text NOT NULL,
 dimension text NOT NULL CHECK(dimension IN ('CASH','PRINCIPAL','GOAL_OWNERSHIP','INCOME_QUALIFICATION','DEBT_MEMO')),
 currency text NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'), account_id uuid NOT NULL,
 direction text NOT NULL CHECK(direction IN ('DEBIT','CREDIT')), amount_cents cents NOT NULL CHECK(amount_cents>0),
 PRIMARY KEY(environment_id,user_id,journal_id,leg_ref),
 FOREIGN KEY(environment_id,user_id,journal_id) REFERENCES journals(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,account_id) REFERENCES accounts(environment_id,user_id,id)
);
CREATE TABLE receipts (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, operation_id uuid NOT NULL, bank_operation_id uuid NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original)>0), created_at timestamptz NOT NULL, PRIMARY KEY(environment_id,user_id,id),
 UNIQUE(environment_id,user_id,operation_id), FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id)
);
CREATE TABLE bank_observations (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, operation_id uuid NOT NULL, status text NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original)>0), observed_at timestamptz NOT NULL, PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id)
);
CREATE TABLE outbox (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, id uuid NOT NULL, aggregate_id uuid NOT NULL, aggregate_version bigint NOT NULL CHECK(aggregate_version>0),
 kind text NOT NULL, payload bytea NOT NULL CHECK(octet_length(payload)>0), created_at timestamptz NOT NULL, processed_at timestamptz,
 PRIMARY KEY(environment_id,user_id,id), UNIQUE(environment_id,user_id,aggregate_id,aggregate_version,kind),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);

-- +goose StatementBegin
CREATE FUNCTION reject_immutable_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable % row',TG_TABLE_NAME USING ERRCODE='23514'; END;
$$;
CREATE FUNCTION protect_version_original() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'status'-'is_current') IS DISTINCT FROM (to_jsonb(OLD)-'status'-'is_current') THEN
  RAISE EXCEPTION 'immutable reviewed version' USING ERRCODE='23514';
 END IF;
 IF OLD.status='REVOKED' AND NEW.status<>'REVOKED' OR OLD.status='PAUSED' AND NEW.status='ACTIVE' THEN
  RAISE EXCEPTION 'stopped version cannot resume' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END; $$;
CREATE FUNCTION protect_operation_original() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR ROW(NEW.environment_id,NEW.user_id,NEW.id,NEW.plan_id,NEW.policy_id,NEW.policy_version,NEW.grant_id,NEW.grant_version,NEW.command_kind,NEW.idempotency_key,NEW.request_hash,NEW.request,NEW.bank_namespace,NEW.bank_key,NEW.bank_request_hash,NEW.bank_request,NEW.dependencies,NEW.created_at,NEW.permit_id)
 IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.id,OLD.plan_id,OLD.policy_id,OLD.policy_version,OLD.grant_id,OLD.grant_version,OLD.command_kind,OLD.idempotency_key,OLD.request_hash,OLD.request,OLD.bank_namespace,OLD.bank_key,OLD.bank_request_hash,OLD.bank_request,OLD.dependencies,OLD.created_at,OLD.permit_id) THEN
  RAISE EXCEPTION 'immutable complete operation request' USING ERRCODE='23514';
 END IF;
 IF OLD.permit_signature IS NOT NULL AND NEW.permit_signature IS DISTINCT FROM OLD.permit_signature THEN RAISE EXCEPTION 'immutable signed permit' USING ERRCODE='23514'; END IF;
 IF OLD.status IN ('SETTLED','REJECTED','CANCELLED') AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN RAISE EXCEPTION 'terminal operation is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END; $$;
CREATE FUNCTION protect_reservation_original() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') OR OLD.state<>'RESERVED' AND NEW.state<>OLD.state THEN
  RAISE EXCEPTION 'immutable income reservation command or terminal result' USING ERRCODE='23514';
 END IF; RETURN NEW;
END; $$;
CREATE FUNCTION protect_posted_journal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
 IF TG_TABLE_NAME='journals' THEN st:=OLD.status;
 ELSE SELECT status INTO st FROM journals WHERE environment_id=OLD.environment_id AND user_id=OLD.user_id AND id=OLD.journal_id; END IF;
 IF st='POSTED' THEN RAISE EXCEPTION 'posted journal and legs are immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END; $$;
CREATE FUNCTION reject_posted_leg_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
 SELECT status INTO st FROM journals WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.journal_id;
 IF st='POSTED' THEN RAISE EXCEPTION 'post legs before sealing journal' USING ERRCODE='23514'; END IF; RETURN NEW;
END; $$;
CREATE FUNCTION check_journal_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e uuid;u uuid;j uuid;st text;n bigint;
BEGIN
 e:=COALESCE(NEW.environment_id,OLD.environment_id);u:=COALESCE(NEW.user_id,OLD.user_id);
 IF TG_TABLE_NAME='journals' THEN j:=COALESCE(NEW.id,OLD.id);ELSE j:=COALESCE(NEW.journal_id,OLD.journal_id);END IF;
 SELECT status INTO st FROM journals WHERE environment_id=e AND user_id=u AND id=j;
 IF st='POSTED' THEN
  SELECT count(*) INTO n FROM postings WHERE environment_id=e AND user_id=u AND journal_id=j;
  IF n=0 OR EXISTS(SELECT 1 FROM postings WHERE environment_id=e AND user_id=u AND journal_id=j GROUP BY dimension,currency HAVING SUM(CASE direction WHEN 'DEBIT' THEN amount_cents::numeric ELSE -amount_cents::numeric END)<>0 OR SUM(CASE direction WHEN 'DEBIT' THEN amount_cents::numeric ELSE 0 END)>9000000000000000) THEN RAISE EXCEPTION 'journal must balance independently by dimension and currency' USING ERRCODE='23514';END IF;
 END IF; RETURN NULL;
END; $$;
CREATE FUNCTION check_one_income(e uuid,u uuid,o uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE original numeric;parts numeric;
BEGIN
 SELECT amount_cents INTO original FROM income_origins WHERE environment_id=e AND user_id=u AND id=o;
 IF original IS NULL THEN RETURN;END IF;
 SELECT COALESCE(SUM(spent_cents::numeric+assigned_cents::numeric+reserved_cents::numeric+available_cents::numeric),0) INTO parts FROM income_locations WHERE environment_id=e AND user_id=u AND origin_id=o;
 IF parts<>original THEN RAISE EXCEPTION 'income origin must conserve across all locations' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM income_locations l WHERE l.environment_id=e AND l.user_id=u AND l.origin_id=o AND l.reserved_cents::numeric<>l.legacy_reserved_cents::numeric+COALESCE((SELECT SUM(c.amount_cents::numeric) FROM income_claims c JOIN income_reservations r USING(environment_id,user_id,action_id) WHERE c.environment_id=e AND c.user_id=u AND c.fragment_id=l.id AND r.state='RESERVED'),0)) THEN RAISE EXCEPTION 'income reserved bucket differs from live exact claims' USING ERRCODE='23514';END IF;
END; $$;
CREATE FUNCTION check_user_cash_coverage(e uuid,u uuid) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM accounts a WHERE a.environment_id=e AND a.user_id=u AND a.balance_cents::numeric-a.goal_owned_cents::numeric<COALESCE((SELECT SUM(l.available_cents::numeric+l.reserved_cents::numeric) FROM income_locations l WHERE l.environment_id=e AND l.user_id=u AND l.account_id=a.id),0)) THEN RAISE EXCEPTION 'income and reservations exceed exact unowned account cash' USING ERRCODE='23514';END IF;
END; $$;
CREATE FUNCTION check_account_coverage() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM check_user_cash_coverage(COALESCE(NEW.environment_id,OLD.environment_id),COALESCE(NEW.user_id,OLD.user_id));RETURN NULL;END; $$;
CREATE FUNCTION check_income_conservation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e uuid;u uuid;o uuid;rec record;
BEGIN
 e:=COALESCE(NEW.environment_id,OLD.environment_id);u:=COALESCE(NEW.user_id,OLD.user_id);
 IF TG_TABLE_NAME='income_origins' THEN PERFORM check_one_income(e,u,COALESCE(NEW.id,OLD.id));
 ELSIF TG_TABLE_NAME='income_locations' THEN PERFORM check_one_income(e,u,COALESCE(NEW.origin_id,OLD.origin_id));
 ELSIF TG_TABLE_NAME='income_claims' THEN SELECT origin_id INTO o FROM income_locations WHERE environment_id=e AND user_id=u AND id=COALESCE(NEW.fragment_id,OLD.fragment_id);PERFORM check_one_income(e,u,o);
 ELSE FOR rec IN SELECT l.origin_id FROM income_claims c JOIN income_locations l ON(l.environment_id=c.environment_id AND l.user_id=c.user_id AND l.id=c.fragment_id) WHERE c.environment_id=e AND c.user_id=u AND c.action_id=COALESCE(NEW.action_id,OLD.action_id) LOOP PERFORM check_one_income(e,u,rec.origin_id);END LOOP;
 END IF; PERFORM check_user_cash_coverage(e,u); RETURN NULL;
END; $$;
-- +goose StatementEnd

CREATE TRIGGER immutable_origin BEFORE UPDATE OR DELETE ON income_origins FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_confirmation BEFORE UPDATE OR DELETE ON confirmations FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_plan BEFORE UPDATE OR DELETE ON plans FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_receipt BEFORE UPDATE OR DELETE ON receipts FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_bank_observation BEFORE UPDATE OR DELETE ON bank_observations FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_policy_version BEFORE UPDATE OR DELETE ON policies FOR EACH ROW EXECUTE FUNCTION protect_version_original();
CREATE TRIGGER immutable_grant_version BEFORE UPDATE OR DELETE ON grants FOR EACH ROW EXECUTE FUNCTION protect_version_original();
CREATE TRIGGER immutable_operation BEFORE UPDATE OR DELETE ON operations FOR EACH ROW EXECUTE FUNCTION protect_operation_original();
CREATE TRIGGER immutable_reservation BEFORE UPDATE OR DELETE ON income_reservations FOR EACH ROW EXECUTE FUNCTION protect_reservation_original();
CREATE TRIGGER immutable_claim BEFORE UPDATE OR DELETE ON income_claims FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_posted_journal BEFORE UPDATE OR DELETE ON journals FOR EACH ROW EXECUTE FUNCTION protect_posted_journal();
CREATE TRIGGER immutable_posted_leg BEFORE UPDATE OR DELETE ON postings FOR EACH ROW EXECUTE FUNCTION protect_posted_journal();
CREATE TRIGGER insert_before_posting BEFORE INSERT ON postings FOR EACH ROW EXECUTE FUNCTION reject_posted_leg_insert();
CREATE CONSTRAINT TRIGGER balanced_journal AFTER INSERT OR UPDATE ON journals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_journal_balance();
CREATE CONSTRAINT TRIGGER balanced_posting AFTER INSERT OR UPDATE OR DELETE ON postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_journal_balance();
CREATE CONSTRAINT TRIGGER conserved_origin AFTER INSERT OR UPDATE OR DELETE ON income_origins DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_income_conservation();
CREATE CONSTRAINT TRIGGER conserved_location AFTER INSERT OR UPDATE OR DELETE ON income_locations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_income_conservation();
CREATE CONSTRAINT TRIGGER conserved_reservation AFTER INSERT OR UPDATE OR DELETE ON income_reservations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_income_conservation();
CREATE CONSTRAINT TRIGGER conserved_claim AFTER INSERT OR UPDATE OR DELETE ON income_claims DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_income_conservation();
CREATE CONSTRAINT TRIGGER covered_account AFTER INSERT OR UPDATE OR DELETE ON accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_account_coverage();

-- +goose StatementBegin
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='zygo_app') THEN
  GRANT USAGE ON SCHEMA public TO zygo_app;
  GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO zygo_app;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT,INSERT,UPDATE ON TABLES TO zygo_app;
  REVOKE ALL ON goose_db_version FROM zygo_app;
  REVOKE CREATE ON SCHEMA public FROM zygo_app;
 END IF;
END; $$;
-- +goose StatementEnd

-- +goose Down
DROP TABLE outbox,bank_observations,receipts,postings,journals,jobs,income_claims,income_reservations,operations,plans;
ALTER TABLE goals DROP CONSTRAINT goal_policy_fk;
DROP TABLE grants,policies,goals,confirmations,income_locations,income_origins,accounts,users,environments;
DROP FUNCTION check_income_conservation(),check_account_coverage(),check_user_cash_coverage(uuid,uuid),check_one_income(uuid,uuid,uuid),check_journal_balance(),reject_posted_leg_insert(),protect_posted_journal(),protect_reservation_original(),protect_operation_original(),protect_version_original(),reject_immutable_row();
DROP DOMAIN hash256,cents;
