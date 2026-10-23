-- Frozen additive bank v2 migration; applied only by explicit MigrateV2.
-- Additive bank v2 state; 001 originals and their digest domains are untouched.
-- The explicit migrator runs the whole file in one transaction. This lock
-- closes the backfill/trigger-install gap while genuine v1 writes continue.
LOCK TABLE bank.operations IN SHARE ROW EXCLUSIVE MODE;
CREATE TABLE IF NOT EXISTS bank.v2_product_terms (
 namespace text NOT NULL, product_id text NOT NULL, version bigint NOT NULL CHECK(version>0),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072), original_hash text NOT NULL CHECK(original_hash ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(namespace,product_id,version)
);
CREATE TABLE IF NOT EXISTS bank.v2_product_availability (
 catalog_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 namespace text NOT NULL, product_id text NOT NULL, terms_version bigint NOT NULL, version bigint NOT NULL CHECK(version>0),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072), original_hash text NOT NULL CHECK(original_hash ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(namespace,product_id,terms_version,version),
 FOREIGN KEY(namespace,product_id,terms_version) REFERENCES bank.v2_product_terms(namespace,product_id,version)
);
CREATE TABLE IF NOT EXISTS bank.v2_operations (
 sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 namespace text NOT NULL, user_id text NOT NULL, operation_key text NOT NULL, operation_id text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('TRANSFER_INTERNAL','ALLOCATE_GOAL','TRANSFER_GOAL','PURCHASE_ASSET','REDEEM_ASSET','MATURE_ASSET','PAY_RECURRING')),
 business_key text NOT NULL, request_original bytea NOT NULL CHECK(octet_length(request_original) BETWEEN 1 AND 131072),
 request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL CHECK(status IN ('ACCEPTED','SETTLED','REJECTED')), version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 accepted_at timestamptz, available_at timestamptz, settled_at timestamptz, rejection_code text NOT NULL DEFAULT '',
 position_original bytea,
 PRIMARY KEY(namespace,operation_key), UNIQUE(namespace,operation_id),
 CHECK((status='REJECTED' AND accepted_at IS NULL AND available_at IS NULL AND settled_at IS NULL AND rejection_code<>'') OR
       (status='ACCEPTED' AND accepted_at IS NOT NULL AND available_at IS NOT NULL AND settled_at IS NULL AND rejection_code='') OR
       (status='SETTLED' AND accepted_at IS NOT NULL AND available_at IS NOT NULL AND settled_at=available_at AND rejection_code=''))
);
CREATE UNIQUE INDEX IF NOT EXISTS v2_one_effect_per_business ON bank.v2_operations(namespace,user_id,kind,business_key) WHERE status<>'REJECTED';
CREATE INDEX IF NOT EXISTS v2_inventory ON bank.v2_operations(namespace,user_id,sequence);
CREATE INDEX IF NOT EXISTS v2_due_operations ON bank.v2_operations(namespace,user_id,available_at,sequence) WHERE status='ACCEPTED';

CREATE TABLE IF NOT EXISTS bank.v2_position_openings (
 namespace text NOT NULL, user_id text NOT NULL, position_id text NOT NULL,
 opening_key text NOT NULL, original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072),
 original_hash text NOT NULL CHECK(original_hash ~ '^[0-9a-f]{64}$'), principal_cents bigint NOT NULL CHECK(principal_cents BETWEEN 1 AND 9000000000000000),
 product_id text NOT NULL, terms_version bigint NOT NULL, goal_id text NOT NULL, return_account_id text NOT NULL,
 purchased_at timestamptz NOT NULL, maturity_at timestamptz,
 PRIMARY KEY(namespace,user_id,position_id), UNIQUE(namespace,user_id,opening_key),
 FOREIGN KEY(namespace,user_id,return_account_id) REFERENCES bank.accounts(namespace,user_id,account_id),
 FOREIGN KEY(namespace,product_id,terms_version) REFERENCES bank.v2_product_terms(namespace,product_id,version),
 CHECK(maturity_at IS NULL OR maturity_at>purchased_at)
);
CREATE TABLE IF NOT EXISTS bank.v2_position_state (
 namespace text NOT NULL, user_id text NOT NULL, position_id text NOT NULL,
 closing_key text, closed boolean NOT NULL DEFAULT false,
 PRIMARY KEY(namespace,user_id,position_id), UNIQUE(namespace,closing_key),
 FOREIGN KEY(namespace,user_id,position_id) REFERENCES bank.v2_position_openings(namespace,user_id,position_id),
 CHECK(NOT closed OR closing_key IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS bank.v2_goal_principal (
 namespace text NOT NULL, user_id text NOT NULL, goal_id text NOT NULL,
 principal_cents bigint NOT NULL DEFAULT 0 CHECK(principal_cents BETWEEN 0 AND 9000000000000000),
 loss_cents bigint NOT NULL DEFAULT 0 CHECK(loss_cents BETWEEN 0 AND 9000000000000000),
 PRIMARY KEY(namespace,user_id,goal_id), FOREIGN KEY(namespace,user_id,goal_id) REFERENCES bank.goals(namespace,user_id,goal_id)
);
CREATE TABLE IF NOT EXISTS bank.v2_goal_lots (
 namespace text NOT NULL, user_id text NOT NULL, goal_id text NOT NULL, account_id text NOT NULL,
 source_kind text NOT NULL CHECK(source_kind IN ('INCOME','PRINCIPAL_RETURN')), source_id text NOT NULL,
 available_cents bigint NOT NULL CHECK(available_cents BETWEEN 0 AND 9000000000000000),
 reserved_cents bigint NOT NULL DEFAULT 0 CHECK(reserved_cents BETWEEN 0 AND available_cents),
 PRIMARY KEY(namespace,user_id,goal_id,account_id,source_kind,source_id),
 FOREIGN KEY(namespace,user_id,goal_id) REFERENCES bank.goals(namespace,user_id,goal_id),
 FOREIGN KEY(namespace,user_id,account_id) REFERENCES bank.accounts(namespace,user_id,account_id)
);
ALTER TABLE bank.income_locations ADD COLUMN IF NOT EXISTS spent_cents bigint NOT NULL DEFAULT 0 CHECK(spent_cents>=0);
ALTER TABLE bank.income_locations ADD COLUMN IF NOT EXISTS assigned_reserved_cents bigint NOT NULL DEFAULT 0 CHECK(assigned_reserved_cents BETWEEN 0 AND assigned_cents);

CREATE TABLE IF NOT EXISTS bank.v2_principal_returns (
 namespace text NOT NULL, user_id text NOT NULL, operation_key text NOT NULL, position_id text NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072), original_hash text NOT NULL CHECK(original_hash ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(namespace,user_id,operation_key), UNIQUE(namespace,user_id,position_id),
 FOREIGN KEY(namespace,operation_key) REFERENCES bank.v2_operations(namespace,operation_key),
 FOREIGN KEY(namespace,user_id,position_id) REFERENCES bank.v2_position_openings(namespace,user_id,position_id)
);
CREATE TABLE IF NOT EXISTS bank.v2_return_locations (
 namespace text NOT NULL, user_id text NOT NULL, operation_key text NOT NULL, account_id text NOT NULL,
 available_cents bigint NOT NULL CHECK(available_cents BETWEEN 0 AND 9000000000000000),
 reserved_cents bigint NOT NULL DEFAULT 0 CHECK(reserved_cents BETWEEN 0 AND available_cents),
 PRIMARY KEY(namespace,user_id,operation_key,account_id),
 FOREIGN KEY(namespace,user_id,operation_key) REFERENCES bank.v2_principal_returns(namespace,user_id,operation_key),
 FOREIGN KEY(namespace,user_id,account_id) REFERENCES bank.accounts(namespace,user_id,account_id)
);
CREATE TABLE IF NOT EXISTS bank.v2_liability_originals (
 namespace text NOT NULL, user_id text NOT NULL, liability_id text NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072), original_hash text NOT NULL CHECK(original_hash ~ '^[0-9a-f]{64}$'),
 final_total_cents bigint NOT NULL CHECK(final_total_cents BETWEEN 1 AND 9000000000000000),
 PRIMARY KEY(namespace,user_id,liability_id)
);
CREATE TABLE IF NOT EXISTS bank.v2_liability_state (
 namespace text NOT NULL, user_id text NOT NULL, liability_id text NOT NULL,
 paid_cents bigint NOT NULL DEFAULT 0 CHECK(paid_cents BETWEEN 0 AND 9000000000000000),
 reserved_cents bigint NOT NULL DEFAULT 0 CHECK(reserved_cents BETWEEN 0 AND 9000000000000000),
 PRIMARY KEY(namespace,user_id,liability_id),
 FOREIGN KEY(namespace,user_id,liability_id) REFERENCES bank.v2_liability_originals(namespace,user_id,liability_id)
);
CREATE TABLE IF NOT EXISTS bank.v2_quotes (
 namespace text NOT NULL, user_id text NOT NULL, quote_id text NOT NULL, position_id text NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072), original_hash text NOT NULL CHECK(original_hash ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(namespace,user_id,quote_id),
 FOREIGN KEY(namespace,user_id,position_id) REFERENCES bank.v2_position_openings(namespace,user_id,position_id)
);
CREATE TABLE IF NOT EXISTS bank.v2_resource_holds (
 namespace text NOT NULL, user_id text NOT NULL, operation_key text NOT NULL,
 resource_kind text NOT NULL CHECK(resource_kind IN ('CASH','INCOME','ASSIGNED','GOAL','GOAL_LOT','RETURN','INCOMING_CASH','INCOMING_GOAL','POSITION','LIABILITY')),
 resource_id text NOT NULL, resource_detail text NOT NULL DEFAULT '',
 amount_cents bigint NOT NULL CHECK(amount_cents BETWEEN 1 AND 9000000000000000),
 state text NOT NULL DEFAULT 'RESERVED' CHECK(state IN ('RESERVED','COMMITTED')),
 PRIMARY KEY(namespace,operation_key,resource_kind,resource_id,resource_detail),
 FOREIGN KEY(namespace,operation_key) REFERENCES bank.v2_operations(namespace,operation_key)
);
CREATE INDEX IF NOT EXISTS v2_active_resources ON bank.v2_resource_holds(namespace,user_id,resource_kind,resource_id) WHERE state='RESERVED';
CREATE TABLE IF NOT EXISTS bank.v2_postings (
 id text PRIMARY KEY, namespace text NOT NULL, operation_key text NOT NULL,
 dimension text NOT NULL CHECK(dimension IN ('ECONOMIC','GOAL_OWNERSHIP','INCOME_LOCATION','LIABILITY')),
 ledger_id text NOT NULL, direction text NOT NULL CHECK(direction IN ('DEBIT','CREDIT')),
 amount_cents bigint NOT NULL CHECK(amount_cents BETWEEN 1 AND 9000000000000000), occurred_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(namespace,operation_key) REFERENCES bank.v2_operations(namespace,operation_key)
);

CREATE OR REPLACE FUNCTION bank.v2_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'bank v2 original is immutable'; END $$;
CREATE OR REPLACE FUNCTION bank.v2_operation_original() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'bank v2 original cannot be deleted'; END IF;
 IF OLD.namespace<>NEW.namespace OR OLD.user_id<>NEW.user_id OR OLD.operation_key<>NEW.operation_key OR OLD.operation_id<>NEW.operation_id OR
    OLD.kind<>NEW.kind OR OLD.business_key<>NEW.business_key OR OLD.request_original<>NEW.request_original OR OLD.request_hash<>NEW.request_hash OR
    OLD.sequence<>NEW.sequence OR OLD.accepted_at IS DISTINCT FROM NEW.accepted_at OR OLD.available_at IS DISTINCT FROM NEW.available_at OR
    OLD.position_original IS DISTINCT FROM NEW.position_original OR OLD.rejection_code<>NEW.rejection_code OR
    OLD.status<>'ACCEPTED' OR NEW.status<>'SETTLED' OR NEW.version<>OLD.version+1
 THEN RAISE EXCEPTION 'invalid mutation of bank v2 original'; END IF;
 RETURN NEW;
END $$;

-- Cross-protocol ordering/capacity guards do not rewrite a v1 original. They
-- prevent a new v1 write from stepping around a committed v2 hold or reusing
-- its original key. No guard consults application projections or permissions.
CREATE OR REPLACE FUNCTION bank.v2_guard_v1_write() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE incoming bigint; pending bigint; balance bigint; destination text;
BEGIN
 IF TG_OP='INSERT' THEN
  IF EXISTS(SELECT 1 FROM bank.v2_operations WHERE namespace=NEW.namespace AND (operation_key=NEW.operation_key OR operation_id=NEW.operation_id)) THEN
   RAISE EXCEPTION 'bank key belongs to a different protocol original' USING ERRCODE='23505';
  END IF;
  IF NEW.status='ACCEPTED' THEN
   IF EXISTS(SELECT 1 FROM bank.v2_operations WHERE namespace=NEW.namespace AND user_id=NEW.user_id AND status='ACCEPTED' AND available_at<=NEW.accepted_at) THEN
    RAISE EXCEPTION 'earlier bank v2 operation requires settlement';
   END IF;
   IF EXISTS(SELECT 1 FROM bank.v2_position_openings p JOIN bank.v2_position_state s USING(namespace,user_id,position_id)
     WHERE p.namespace=NEW.namespace AND p.user_id=NEW.user_id AND NOT s.closed AND s.closing_key IS NULL AND p.maturity_at IS NOT NULL AND
     p.maturity_at+((convert_from(p.original,'UTF8')::jsonb#>>'{terms,redemption_delay_days}')::integer*interval '1 day')<=NEW.accepted_at) THEN
    RAISE EXCEPTION 'contractual maturity must be reconciled before later bank effects';
   END IF;
   IF NEW.request->>'kind'='TRANSFER' THEN
    destination:=NEW.request->>'destination_account_id';
    SELECT balance_cents INTO balance FROM bank.accounts WHERE namespace=NEW.namespace AND user_id=NEW.user_id AND account_id=destination;
    SELECT coalesce(sum(amount_cents),0) INTO incoming FROM bank.v2_resource_holds WHERE namespace=NEW.namespace AND user_id=NEW.user_id AND resource_kind='INCOMING_CASH' AND resource_id=destination AND state='RESERVED';
    SELECT coalesce(sum((request->>'amount_cents')::bigint),0) INTO pending FROM bank.operations WHERE namespace=NEW.namespace AND user_id=NEW.user_id AND status='ACCEPTED' AND request->>'kind'='TRANSFER' AND request->>'destination_account_id'=destination;
    IF balance::numeric+incoming::numeric+pending::numeric+(NEW.request->>'amount_cents')::numeric>9000000000000000 THEN
     RAISE EXCEPTION 'bank v2 incoming capacity already reserved';
    END IF;
   END IF;
  END IF;
 ELSIF NEW.status='SETTLED' AND OLD.status='ACCEPTED' THEN
  IF EXISTS(SELECT 1 FROM bank.v2_operations WHERE namespace=NEW.namespace AND user_id=NEW.user_id AND status='ACCEPTED' AND available_at<NEW.available_at) THEN
   RAISE EXCEPTION 'earlier bank v2 operation requires settlement';
  END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='v2_guard_v1_write' AND tgrelid='bank.operations'::regclass) THEN
  CREATE TRIGGER v2_guard_v1_write BEFORE INSERT OR UPDATE ON bank.operations FOR EACH ROW EXECUTE FUNCTION bank.v2_guard_v1_write();
 END IF;
END $$;
CREATE OR REPLACE FUNCTION bank.v2_hold_original() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.namespace<>NEW.namespace OR OLD.user_id<>NEW.user_id OR OLD.operation_key<>NEW.operation_key OR
    OLD.resource_kind<>NEW.resource_kind OR OLD.resource_id<>NEW.resource_id OR OLD.resource_detail<>NEW.resource_detail OR OLD.amount_cents<>NEW.amount_cents OR
    OLD.state<>'RESERVED' OR NEW.state<>'COMMITTED'
 THEN RAISE EXCEPTION 'invalid mutation of bank v2 hold'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION bank.v2_position_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.namespace<>NEW.namespace OR OLD.user_id<>NEW.user_id OR OLD.position_id<>NEW.position_id OR OLD.closed OR
    (OLD.closing_key IS NOT NULL AND OLD.closing_key IS DISTINCT FROM NEW.closing_key) OR (NEW.closed AND NEW.closing_key IS NULL)
 THEN RAISE EXCEPTION 'invalid position close transition'; END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['v2_product_terms','v2_product_availability','v2_position_openings','v2_principal_returns','v2_liability_originals','v2_quotes','v2_postings'] LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname=tab||'_original' AND tgrelid=('bank.'||tab)::regclass) THEN
   EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON bank.%I FOR EACH ROW EXECUTE FUNCTION bank.v2_immutable()',tab||'_original',tab);
  END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='v2_operations_original' AND tgrelid='bank.v2_operations'::regclass) THEN
  CREATE TRIGGER v2_operations_original BEFORE UPDATE OR DELETE ON bank.v2_operations FOR EACH ROW EXECUTE FUNCTION bank.v2_operation_original();
  CREATE TRIGGER v2_holds_original BEFORE UPDATE OR DELETE ON bank.v2_resource_holds FOR EACH ROW EXECUTE FUNCTION bank.v2_hold_original();
  CREATE TRIGGER v2_position_transition BEFORE UPDATE OR DELETE ON bank.v2_position_state FOR EACH ROW EXECUTE FUNCTION bank.v2_position_transition();
 END IF;
END $$;

-- Derive a new projection only from genuine already-settled immutable v1
-- requests. It does not manufacture or modify any opening or old bank original.
INSERT INTO bank.v2_goal_lots(namespace,user_id,goal_id,account_id,source_kind,source_id,available_cents)
 SELECT namespace,user_id,request->>'goal_id',request->>'source_account_id','INCOME',request->>'income_origin_id',sum((request->>'amount_cents')::bigint)
 FROM bank.operations WHERE status='SETTLED' AND request->>'kind'='ALLOCATE_GOAL'
 GROUP BY namespace,user_id,request->>'goal_id',request->>'source_account_id',request->>'income_origin_id'
 ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION bank.v2_mirror_v1_goal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status='SETTLED' AND OLD.status='ACCEPTED' AND NEW.request->>'kind'='ALLOCATE_GOAL' THEN
  INSERT INTO bank.v2_goal_lots(namespace,user_id,goal_id,account_id,source_kind,source_id,available_cents)
   VALUES(NEW.namespace,NEW.user_id,NEW.request->>'goal_id',NEW.request->>'source_account_id','INCOME',NEW.request->>'income_origin_id',(NEW.request->>'amount_cents')::bigint)
   ON CONFLICT(namespace,user_id,goal_id,account_id,source_kind,source_id) DO UPDATE SET available_cents=bank.v2_goal_lots.available_cents+EXCLUDED.available_cents;
 END IF;
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='v2_mirror_v1_goal' AND tgrelid='bank.operations'::regclass) THEN
  CREATE TRIGGER v2_mirror_v1_goal AFTER UPDATE ON bank.operations FOR EACH ROW EXECUTE FUNCTION bank.v2_mirror_v1_goal();
 END IF;
END $$;
