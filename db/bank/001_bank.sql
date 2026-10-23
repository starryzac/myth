-- Dedicated simulated-bank database, owned only by the bank process role.
-- Application roles receive no privileges on this schema.
CREATE SCHEMA IF NOT EXISTS bank;
CREATE TABLE IF NOT EXISTS bank.opening_anchors (
 namespace text NOT NULL, user_id text NOT NULL, account_id text NOT NULL,
 opening_cents bigint NOT NULL CHECK(opening_cents BETWEEN 0 AND 9000000000000000),
 PRIMARY KEY(namespace,user_id,account_id)
);
CREATE TABLE IF NOT EXISTS bank.accounts (
 namespace text NOT NULL, user_id text NOT NULL, account_id text NOT NULL,
 balance_cents bigint NOT NULL CHECK(balance_cents BETWEEN 0 AND 9000000000000000),
 reserved_cents bigint NOT NULL DEFAULT 0 CHECK(reserved_cents >= 0 AND reserved_cents <= balance_cents),
 PRIMARY KEY(namespace,user_id,account_id)
);
CREATE TABLE IF NOT EXISTS bank.origins (
 namespace text NOT NULL, user_id text NOT NULL, origin_id text NOT NULL,
 original_account_id text NOT NULL, original_cents bigint NOT NULL CHECK(original_cents > 0),
 occurred_at timestamptz NOT NULL, evidence_hash text NOT NULL,
 PRIMARY KEY(namespace,user_id,origin_id)
);
CREATE TABLE IF NOT EXISTS bank.income_locations (
 namespace text NOT NULL, user_id text NOT NULL, origin_id text NOT NULL, account_id text NOT NULL,
 available_cents bigint NOT NULL CHECK(available_cents >= 0), assigned_cents bigint NOT NULL DEFAULT 0 CHECK(assigned_cents>=0), reserved_cents bigint NOT NULL DEFAULT 0 CHECK(reserved_cents >= 0 AND reserved_cents <= available_cents),
 PRIMARY KEY(namespace,user_id,origin_id,account_id),
 FOREIGN KEY(namespace,user_id,origin_id) REFERENCES bank.origins(namespace,user_id,origin_id),
 FOREIGN KEY(namespace,user_id,account_id) REFERENCES bank.accounts(namespace,user_id,account_id)
);
CREATE TABLE IF NOT EXISTS bank.goals (
 namespace text NOT NULL, user_id text NOT NULL, goal_id text NOT NULL, account_id text NOT NULL,
 balance_cents bigint NOT NULL DEFAULT 0 CHECK(balance_cents BETWEEN 0 AND 9000000000000000),
 PRIMARY KEY(namespace,user_id,goal_id),
 FOREIGN KEY(namespace,user_id,account_id) REFERENCES bank.accounts(namespace,user_id,account_id)
);
CREATE TABLE IF NOT EXISTS bank.operations (
 sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 namespace text NOT NULL, user_id text NOT NULL, operation_key text NOT NULL, operation_id text NOT NULL,
 request_hash text NOT NULL CHECK(length(request_hash)=64), request jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('ACCEPTED','SETTLED','REJECTED')),
 version bigint NOT NULL DEFAULT 1, accepted_at timestamptz,
 available_at timestamptz, settled_at timestamptz, rejection_code text NOT NULL DEFAULT '',
 PRIMARY KEY(namespace,operation_key), UNIQUE(namespace,operation_id),
 CHECK((status='REJECTED' AND accepted_at IS NULL AND available_at IS NULL AND settled_at IS NULL AND rejection_code <> '') OR
       (status='ACCEPTED' AND accepted_at IS NOT NULL AND available_at IS NOT NULL AND settled_at IS NULL AND rejection_code = '') OR
       (status='SETTLED' AND accepted_at IS NOT NULL AND available_at IS NOT NULL AND settled_at=available_at AND rejection_code = ''))
);
CREATE INDEX IF NOT EXISTS bank_user_inventory ON bank.operations(namespace,user_id,sequence);
CREATE TABLE IF NOT EXISTS bank.postings (
 id text PRIMARY KEY, namespace text NOT NULL, operation_key text NOT NULL,
 dimension text NOT NULL CHECK(dimension IN ('ECONOMIC','INCOME_LOCATION','GOAL_OWNERSHIP')),
 ledger_id text NOT NULL, direction text NOT NULL CHECK(direction IN ('DEBIT','CREDIT')),
 amount_cents bigint NOT NULL CHECK(amount_cents > 0), occurred_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(namespace,operation_key) REFERENCES bank.operations(namespace,operation_key),
 UNIQUE(namespace,operation_key,dimension,ledger_id)
);
CREATE OR REPLACE FUNCTION bank.protect_original() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'bank originals cannot be deleted'; END IF;
 IF TG_TABLE_NAME IN ('origins','postings','opening_anchors') THEN RAISE EXCEPTION 'bank original is immutable'; END IF;
 IF OLD.namespace<>NEW.namespace OR OLD.user_id<>NEW.user_id OR OLD.operation_key<>NEW.operation_key OR
    OLD.operation_id<>NEW.operation_id OR OLD.request_hash<>NEW.request_hash OR OLD.request<>NEW.request OR
    OLD.sequence<>NEW.sequence OR OLD.accepted_at IS DISTINCT FROM NEW.accepted_at OR OLD.available_at IS DISTINCT FROM NEW.available_at OR
    OLD.rejection_code<>NEW.rejection_code OR OLD.status<>'ACCEPTED' OR NEW.status<>'SETTLED' OR NEW.version<>OLD.version+1
 THEN RAISE EXCEPTION 'invalid mutation of bank original'; END IF;
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='bank_operations_original' AND tgrelid='bank.operations'::regclass) THEN
 CREATE TRIGGER bank_operations_original BEFORE UPDATE OR DELETE ON bank.operations FOR EACH ROW EXECUTE FUNCTION bank.protect_original();
 CREATE TRIGGER bank_origins_original BEFORE UPDATE OR DELETE ON bank.origins FOR EACH ROW EXECUTE FUNCTION bank.protect_original();
 CREATE TRIGGER bank_postings_original BEFORE UPDATE OR DELETE ON bank.postings FOR EACH ROW EXECUTE FUNCTION bank.protect_original();
 CREATE TRIGGER bank_anchors_original BEFORE UPDATE OR DELETE ON bank.opening_anchors FOR EACH ROW EXECUTE FUNCTION bank.protect_original();
 END IF;
END $$;
