-- Frozen additive native execution contract; existing 1-12 originals remain unchanged.
-- +goose Up
LOCK TABLE operations,goals IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE native_previews (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,kind text NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 1048576),original_hash hash256 NOT NULL,
 effect_hash hash256 NOT NULL,policy_refs jsonb NOT NULL CHECK(jsonb_typeof(policy_refs)='array'),source_hash hash256 NOT NULL,
 dependencies jsonb NOT NULL CHECK(jsonb_typeof(dependencies)='object'),dependencies_hash hash256 NOT NULL,
 bank_request bytea NOT NULL CHECK(octet_length(bank_request) BETWEEN 1 AND 131072),bank_request_hash hash256 NOT NULL,
 created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>created_at),
 PRIMARY KEY(environment_id,user_id,id),FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id)
);
CREATE TABLE native_confirmations (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,preview_id uuid NOT NULL,
 session_id uuid NOT NULL,role text NOT NULL CHECK(role IN ('EXECUTE','RELEASE_SOURCE','RECEIVE_TARGET')),
 policy_id uuid NOT NULL,policy_version bigint NOT NULL CHECK(policy_version>0),
 effect_hash hash256 NOT NULL,child_effect_hashes jsonb NOT NULL CHECK(jsonb_typeof(child_effect_hashes)='array'),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 1048576),original_hash hash256 NOT NULL,
 created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>created_at),
 PRIMARY KEY(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,preview_id) REFERENCES native_previews(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id)
);
CREATE TABLE native_operations (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,command_kind text NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 256),
 request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 1048576),request_hash hash256 NOT NULL,
 bank_namespace text NOT NULL CHECK(length(bank_namespace) BETWEEN 1 AND 128),bank_key text NOT NULL CHECK(length(bank_key) BETWEEN 1 AND 256),
 bank_request bytea NOT NULL CHECK(octet_length(bank_request) BETWEEN 1 AND 131072),bank_request_hash hash256 NOT NULL,effect_hash hash256 NOT NULL,
 dependencies jsonb NOT NULL CHECK(jsonb_typeof(dependencies)='object'),dependencies_hash hash256 NOT NULL,
 plan_id uuid,preview_id uuid NOT NULL,group_id uuid,child_number integer,previous_operation_id uuid,subject_goal_ids uuid[] NOT NULL DEFAULT '{}',
 mode text NOT NULL CHECK(mode IN ('ONCE','STANDING')),confirmation_id uuid,grant_id uuid,grant_version bigint,
 status text NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','SUBMITTING','PENDING_SETTLEMENT','UNKNOWN','RECONCILING','SETTLED','REJECTED','CANCELLED')),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),processing_health text NOT NULL DEFAULT 'HEALTHY',bank_observed_status text,
 receipt_id uuid,permit_id uuid NOT NULL,permit_signature text,reserved_funds_version bigint NOT NULL CHECK(reserved_funds_version>0),
 created_at timestamptz NOT NULL,updated_at timestamptz NOT NULL,accepted_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 CHECK((mode='ONCE' AND confirmation_id IS NOT NULL AND grant_id IS NULL AND grant_version IS NULL) OR
       (mode='STANDING' AND confirmation_id IS NULL AND grant_id IS NOT NULL AND grant_version>0)),
 CHECK((group_id IS NULL AND child_number IS NULL AND previous_operation_id IS NULL) OR (group_id IS NOT NULL AND child_number>0)),
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,command_kind,idempotency_key),UNIQUE(bank_namespace,bank_key),
 UNIQUE(environment_id,user_id,group_id,child_number),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,preview_id) REFERENCES native_previews(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,confirmation_id) REFERENCES native_confirmations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,previous_operation_id) REFERENCES native_operations(environment_id,user_id,id)
);
CREATE TABLE native_jobs (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,operation_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'READY' CHECK(state IN ('READY','RUNNING','FINISHED','EXHAUSTED')),
 lease_owner text,lease_until timestamptz,claim_generation bigint NOT NULL DEFAULT 0 CHECK(claim_generation>=0),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),max_attempts integer NOT NULL DEFAULT 8 CHECK(max_attempts BETWEEN 1 AND 100),
 next_attempt_at timestamptz NOT NULL,last_error text NOT NULL DEFAULT '',paused boolean NOT NULL DEFAULT false,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id),
 CHECK((lease_owner IS NULL)=(lease_until IS NULL))
);
CREATE INDEX native_pending_goal_subjects ON native_operations USING gin(subject_goal_ids) WHERE status NOT IN ('SETTLED','REJECTED','CANCELLED');
CREATE INDEX native_ready_jobs ON native_jobs(next_attempt_at,id) WHERE state IN ('READY','RUNNING') AND NOT paused;
CREATE TABLE native_submission_permits (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,operation_id uuid NOT NULL,permit_id uuid NOT NULL,
 bank_request_hash hash256 NOT NULL,effect_hash hash256 NOT NULL,signature_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 1048576),original_hash hash256 NOT NULL,
 decision_at timestamptz NOT NULL,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,user_id,operation_id),UNIQUE(environment_id,user_id,permit_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id)
);
CREATE TABLE native_income_reservations (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,action_id uuid NOT NULL,
 operation text NOT NULL CHECK(operation IN ('ALLOCATE_GOAL','TRANSFER_INTERNAL','SPEND')),destination_account_id uuid,
 state text NOT NULL CHECK(state IN ('RESERVED','COMMITTED','RELEASED')),
 CHECK((operation='TRANSFER_INTERNAL')=(destination_account_id IS NOT NULL)),
 PRIMARY KEY(environment_id,user_id,action_id),
 FOREIGN KEY(environment_id,user_id,action_id) REFERENCES native_operations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,destination_account_id) REFERENCES accounts(environment_id,user_id,id)
);
CREATE TABLE native_income_claims (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,action_id uuid NOT NULL,fragment_id uuid NOT NULL,amount_cents cents NOT NULL CHECK(amount_cents>0),
 PRIMARY KEY(environment_id,user_id,action_id,fragment_id),
 FOREIGN KEY(environment_id,user_id,action_id) REFERENCES native_income_reservations(environment_id,user_id,action_id),
 FOREIGN KEY(environment_id,user_id,fragment_id) REFERENCES income_locations(environment_id,user_id,id)
);
CREATE TABLE native_goal_balances (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,goal_id uuid NOT NULL,cash_cents cents NOT NULL DEFAULT 0,
 principal_cents cents NOT NULL DEFAULT 0,loss_cents cents NOT NULL DEFAULT 0,
 PRIMARY KEY(environment_id,user_id,goal_id),FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id)
);
CREATE TABLE native_goal_lots (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,goal_id uuid NOT NULL,account_id uuid NOT NULL,
 source_kind text NOT NULL CHECK(source_kind IN ('INCOME','PRINCIPAL_RETURN')),source_id text NOT NULL CHECK(length(source_id) BETWEEN 1 AND 256),
 available_cents cents NOT NULL,reserved_cents cents NOT NULL DEFAULT 0 CHECK(reserved_cents<=available_cents),
 PRIMARY KEY(environment_id,user_id,goal_id,account_id,source_kind,source_id),
 FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,account_id) REFERENCES accounts(environment_id,user_id,id)
);
CREATE TABLE native_holds (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,operation_id uuid NOT NULL,
 resource_kind text NOT NULL CHECK(resource_kind IN ('CASH','ASSIGNED','GOAL','GOAL_LOT','POSITION','INCOMING_CASH','INCOMING_GOAL','LIABILITY')),
 resource_id text NOT NULL CHECK(length(resource_id) BETWEEN 1 AND 256),resource_detail text NOT NULL DEFAULT '' CHECK(length(resource_detail)<=2048),
 amount_cents cents NOT NULL CHECK(amount_cents>0),state text NOT NULL CHECK(state IN ('RESERVED','COMMITTED','RELEASED')),
 PRIMARY KEY(environment_id,user_id,operation_id,resource_kind,resource_id,resource_detail),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id)
);
CREATE INDEX native_active_holds ON native_holds(environment_id,user_id,resource_kind,resource_id) WHERE state='RESERVED';
CREATE TABLE native_positions (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id text NOT NULL,opening_operation_id uuid,bank_key text NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072),original_hash hash256 NOT NULL,
 principal_cents cents NOT NULL CHECK(principal_cents>0),goal_id uuid,return_account_id uuid NOT NULL,
 purchased_at timestamptz NOT NULL,maturity_at timestamptz,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,bank_key),
 FOREIGN KEY(environment_id,user_id,opening_operation_id) REFERENCES native_operations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,return_account_id) REFERENCES accounts(environment_id,user_id,id),
 CHECK(maturity_at IS NULL OR maturity_at>purchased_at)
);
CREATE TABLE native_position_states (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,position_id text NOT NULL,closing_operation_id uuid,closed boolean NOT NULL DEFAULT false,
 PRIMARY KEY(environment_id,user_id,position_id),UNIQUE(environment_id,user_id,closing_operation_id),
 FOREIGN KEY(environment_id,user_id,position_id) REFERENCES native_positions(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,closing_operation_id) REFERENCES native_operations(environment_id,user_id,id),
 CHECK(NOT closed OR closing_operation_id IS NOT NULL)
);
CREATE TABLE native_principal_returns (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,bank_key text NOT NULL,operation_id uuid NOT NULL,position_id text NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072),original_hash hash256 NOT NULL,
 amount_cents cents NOT NULL,occurred_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,bank_key),UNIQUE(environment_id,user_id,operation_id),UNIQUE(environment_id,user_id,position_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,position_id) REFERENCES native_positions(environment_id,user_id,id)
);
CREATE TABLE native_return_locations (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,bank_key text NOT NULL,account_id uuid NOT NULL,
 available_cents cents NOT NULL,reserved_cents cents NOT NULL DEFAULT 0 CHECK(reserved_cents<=available_cents),
 PRIMARY KEY(environment_id,user_id,bank_key,account_id),
 FOREIGN KEY(environment_id,user_id,bank_key) REFERENCES native_principal_returns(environment_id,user_id,bank_key),
 FOREIGN KEY(environment_id,user_id,account_id) REFERENCES accounts(environment_id,user_id,id)
);
CREATE TABLE native_return_claims (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,operation_id uuid NOT NULL,bank_key text NOT NULL,account_id uuid NOT NULL,
 amount_cents cents NOT NULL CHECK(amount_cents>0),state text NOT NULL CHECK(state IN ('RESERVED','COMMITTED','RELEASED')),
 PRIMARY KEY(environment_id,user_id,operation_id,bank_key,account_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,bank_key,account_id) REFERENCES native_return_locations(environment_id,user_id,bank_key,account_id)
);
CREATE TABLE native_bank_observations (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,operation_id uuid NOT NULL,status text NOT NULL CHECK(status IN ('NOT_FOUND','ACCEPTED','SETTLED','REJECTED')),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 1048576),original_hash hash256 NOT NULL,observed_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id)
);
CREATE TABLE native_journals (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,operation_id uuid NOT NULL,kind text NOT NULL,
 status text NOT NULL CHECK(status IN ('DRAFT','POSTED')),created_at timestamptz NOT NULL,posted_at timestamptz,
 CHECK((status='POSTED')=(posted_at IS NOT NULL)),PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,operation_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id)
);
CREATE TABLE native_postings (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,journal_id uuid NOT NULL,leg_ref text NOT NULL,bank_posting_id text NOT NULL,
 dimension text NOT NULL CHECK(dimension IN ('ECONOMIC','GOAL_OWNERSHIP','INCOME_LOCATION','LIABILITY')),
 ledger_id text NOT NULL,currency text NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),direction text NOT NULL CHECK(direction IN ('DEBIT','CREDIT')),
 amount_cents cents NOT NULL CHECK(amount_cents>0),occurred_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,journal_id,leg_ref),UNIQUE(environment_id,user_id,bank_posting_id),
 FOREIGN KEY(environment_id,user_id,journal_id) REFERENCES native_journals(environment_id,user_id,id)
);
CREATE TABLE native_receipts (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,operation_id uuid NOT NULL,journal_id uuid NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 1048576),original_hash hash256 NOT NULL,created_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,operation_id),UNIQUE(environment_id,user_id,journal_id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES native_operations(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,journal_id) REFERENCES native_journals(environment_id,user_id,id)
);
ALTER TABLE native_operations ADD CONSTRAINT native_receipt_fk FOREIGN KEY(environment_id,user_id,receipt_id) REFERENCES native_receipts(environment_id,user_id,id) DEFERRABLE INITIALLY DEFERRED;

-- +goose StatementBegin
CREATE FUNCTION protect_native_operation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'status'-'version'-'processing_health'-'bank_observed_status'-'receipt_id'-'permit_signature'-'updated_at') IS DISTINCT FROM
  (to_jsonb(OLD)-'status'-'version'-'processing_health'-'bank_observed_status'-'receipt_id'-'permit_signature'-'updated_at') THEN
  RAISE EXCEPTION 'immutable native operation original' USING ERRCODE='23514';END IF;
 IF OLD.permit_signature IS NOT NULL AND NEW.permit_signature IS DISTINCT FROM OLD.permit_signature THEN RAISE EXCEPTION 'native permit is immutable' USING ERRCODE='23514';END IF;
 IF OLD.bank_observed_status IN ('ACCEPTED','SETTLED') AND (NEW.bank_observed_status IS NULL OR NEW.bank_observed_status NOT IN ('ACCEPTED','SETTLED')) OR OLD.bank_observed_status='SETTLED' AND NEW.bank_observed_status<>'SETTLED' THEN RAISE EXCEPTION 'native bank acceptance cannot be erased' USING ERRCODE='23514';END IF;
 IF OLD.status IN ('SETTLED','REJECTED','CANCELLED') AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN RAISE EXCEPTION 'native terminal original is immutable' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_operation_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='operations' THEN
  IF EXISTS(SELECT 1 FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.id OR bank_namespace=NEW.bank_namespace AND bank_key=NEW.bank_key) THEN RAISE EXCEPTION 'operation belongs to native protocol' USING ERRCODE='23505';END IF;
 ELSE
  IF cardinality(NEW.subject_goal_ids)>8 OR cardinality(NEW.subject_goal_ids)<>(SELECT count(DISTINCT goal_id) FROM unnest(NEW.subject_goal_ids) AS subject(goal_id)) OR EXISTS(SELECT 1 FROM unnest(NEW.subject_goal_ids) AS subject(goal_id) WHERE NOT EXISTS(SELECT 1 FROM goals g WHERE g.environment_id=NEW.environment_id AND g.user_id=NEW.user_id AND g.id=subject.goal_id)) THEN RAISE EXCEPTION 'native subject goals must belong to same user scope' USING ERRCODE='23514';END IF;
  IF EXISTS(SELECT 1 FROM operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.id OR bank_namespace=NEW.bank_namespace AND bank_key=NEW.bank_key) THEN RAISE EXCEPTION 'operation belongs to v1 protocol' USING ERRCODE='23505';END IF;
 END IF;RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION check_one_income(e uuid,u uuid,o uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE original numeric;parts numeric;
BEGIN
 SELECT amount_cents INTO original FROM income_origins WHERE environment_id=e AND user_id=u AND id=o;
 IF original IS NULL THEN RETURN;END IF;
 SELECT COALESCE(SUM(spent_cents::numeric+assigned_cents::numeric+reserved_cents::numeric+available_cents::numeric),0) INTO parts FROM income_locations WHERE environment_id=e AND user_id=u AND origin_id=o;
 IF parts<>original THEN RAISE EXCEPTION 'income origin must conserve across all locations' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM income_locations l WHERE l.environment_id=e AND l.user_id=u AND l.origin_id=o AND l.reserved_cents::numeric<>l.legacy_reserved_cents::numeric+
 COALESCE((SELECT SUM(c.amount_cents::numeric) FROM income_claims c JOIN income_reservations r USING(environment_id,user_id,action_id) WHERE c.environment_id=e AND c.user_id=u AND c.fragment_id=l.id AND r.state='RESERVED'),0)+
 COALESCE((SELECT SUM(c.amount_cents::numeric) FROM native_income_claims c JOIN native_income_reservations r USING(environment_id,user_id,action_id) WHERE c.environment_id=e AND c.user_id=u AND c.fragment_id=l.id AND r.state='RESERVED'),0)) THEN RAISE EXCEPTION 'income reserved differs from v1 and native claims' USING ERRCODE='23514';END IF;
END $$;
CREATE FUNCTION check_native_resources(e uuid,u uuid) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 PERFORM check_user_cash_coverage(e,u);
 IF EXISTS(SELECT 1 FROM native_holds h WHERE h.environment_id=e AND h.user_id=u AND h.resource_kind IN ('CASH','INCOMING_CASH') AND NOT EXISTS(SELECT 1 FROM accounts a WHERE a.environment_id=e AND a.user_id=u AND a.id::text=h.resource_id)) THEN RAISE EXCEPTION 'native cash hold account outside user scope' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM accounts a WHERE a.environment_id=e AND a.user_id=u AND a.balance_cents::numeric+coalesce((SELECT sum(h.amount_cents::numeric) FROM native_holds h WHERE h.environment_id=e AND h.user_id=u AND h.resource_kind='INCOMING_CASH' AND h.resource_id=a.id::text AND h.state='RESERVED'),0)>9000000000000000) THEN RAISE EXCEPTION 'native incoming account capacity already held' USING ERRCODE='23514';END IF;
 IF EXISTS(WITH claimed AS (
 SELECT c.action_id AS operation_id,l.account_id,c.amount_cents FROM native_income_claims c JOIN native_income_reservations r USING(environment_id,user_id,action_id) JOIN income_locations l ON(l.environment_id=c.environment_id AND l.user_id=c.user_id AND l.id=c.fragment_id) WHERE c.environment_id=e AND c.user_id=u AND r.state='RESERVED'
 UNION ALL SELECT c.operation_id,c.account_id,c.amount_cents FROM native_return_claims c JOIN native_operations o ON(o.environment_id=c.environment_id AND o.user_id=c.user_id AND o.id=c.operation_id) WHERE c.environment_id=e AND c.user_id=u AND c.state='RESERVED' AND coalesce(convert_from(o.bank_request,'UTF8')::jsonb#>>'{effect,source_goal_id}','')='')
 SELECT 1 FROM claimed c GROUP BY c.operation_id,c.account_id HAVING sum(c.amount_cents::numeric)>coalesce((SELECT sum(h.amount_cents::numeric) FROM native_holds h WHERE h.environment_id=e AND h.user_id=u AND h.operation_id=c.operation_id AND h.resource_kind='CASH' AND h.resource_id=c.account_id::text AND h.state='RESERVED'),0)) THEN RAISE EXCEPTION 'native income or unowned return claim requires exact paired cash hold' USING ERRCODE='23514';END IF;
 -- Each native CASH hold includes its classified income, so add back only
 -- its own AVAILABLE claims; unclassified/return use consumes real slack.
 IF EXISTS(SELECT 1 FROM accounts a WHERE a.environment_id=e AND a.user_id=u AND
  a.balance_cents::numeric-a.goal_owned_cents::numeric-
  COALESCE((SELECT sum(h.amount_cents::numeric) FROM native_holds h WHERE h.environment_id=e AND h.user_id=u AND h.resource_kind='CASH' AND h.resource_id=a.id::text AND h.state='RESERVED'),0)+
  COALESCE((SELECT sum(c.amount_cents::numeric) FROM native_income_claims c JOIN native_income_reservations r USING(environment_id,user_id,action_id) JOIN income_locations l ON(l.environment_id=c.environment_id AND l.user_id=c.user_id AND l.id=c.fragment_id) WHERE c.environment_id=e AND c.user_id=u AND l.account_id=a.id AND r.state='RESERVED'),0)+
  COALESCE((SELECT sum(c.amount_cents::numeric) FROM native_return_claims c JOIN native_operations o ON(o.environment_id=c.environment_id AND o.user_id=c.user_id AND o.id=c.operation_id) WHERE c.environment_id=e AND c.user_id=u AND c.account_id=a.id AND c.state='RESERVED' AND coalesce(convert_from(o.bank_request,'UTF8')::jsonb#>>'{effect,source_goal_id}','')=''),0)
  < COALESCE((SELECT sum(l.available_cents::numeric+l.reserved_cents::numeric) FROM income_locations l WHERE l.environment_id=e AND l.user_id=u AND l.account_id=a.id),0)+
  COALESCE((SELECT sum(l.available_cents::numeric) FROM native_return_locations l WHERE l.environment_id=e AND l.user_id=u AND l.account_id=a.id),0)-
  COALESCE((SELECT sum(l.available_cents::numeric) FROM native_goal_lots l WHERE l.environment_id=e AND l.user_id=u AND l.account_id=a.id AND l.source_kind='PRINCIPAL_RETURN'),0))
 THEN RAISE EXCEPTION 'native cash holds consume qualified income slack' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM native_return_locations l WHERE l.environment_id=e AND l.user_id=u AND l.reserved_cents::numeric<>
 COALESCE((SELECT sum(c.amount_cents::numeric) FROM native_return_claims c WHERE c.environment_id=e AND c.user_id=u AND c.bank_key=l.bank_key AND c.account_id=l.account_id AND c.state='RESERVED'),0)) THEN RAISE EXCEPTION 'principal return reserved differs from exact claims' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM native_return_locations l WHERE l.environment_id=e AND l.user_id=u AND
 COALESCE((SELECT sum(c.amount_cents::numeric) FROM native_return_claims c JOIN native_operations o ON(o.environment_id=c.environment_id AND o.user_id=c.user_id AND o.id=c.operation_id) WHERE c.environment_id=e AND c.user_id=u AND c.bank_key=l.bank_key AND c.account_id=l.account_id AND c.state='RESERVED' AND coalesce(convert_from(o.bank_request,'UTF8')::jsonb#>>'{effect,source_goal_id}','')=''),0)>
 l.available_cents::numeric-COALESCE((SELECT sum(g.available_cents::numeric) FROM native_goal_lots g WHERE g.environment_id=e AND g.user_id=u AND g.account_id=l.account_id AND g.source_kind='PRINCIPAL_RETURN' AND g.source_id=l.bank_key),0)) THEN RAISE EXCEPTION 'goal owned principal return cannot fund an unowned request' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM native_principal_returns p WHERE p.environment_id=e AND p.user_id=u AND p.amount_cents::numeric<>
 COALESCE((SELECT sum(l.available_cents::numeric) FROM native_return_locations l WHERE l.environment_id=e AND l.user_id=u AND l.bank_key=p.bank_key),0)+
 COALESCE((SELECT sum(c.amount_cents::numeric) FROM native_return_claims c JOIN native_operations o ON(o.environment_id=c.environment_id AND o.user_id=c.user_id AND o.id=c.operation_id) WHERE c.environment_id=e AND c.user_id=u AND c.bank_key=p.bank_key AND c.state='COMMITTED' AND o.command_kind IN ('PURCHASE_ASSET','PAY_RECURRING')),0)) THEN RAISE EXCEPTION 'principal return must conserve without becoming new income' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM native_goal_lots l WHERE l.environment_id=e AND l.user_id=u AND l.reserved_cents::numeric<>
 COALESCE((SELECT sum(h.amount_cents::numeric) FROM native_holds h WHERE h.environment_id=e AND h.user_id=u AND h.resource_kind='GOAL_LOT' AND h.resource_id=l.goal_id::text AND h.resource_detail::jsonb->>'account_id'=l.account_id::text AND h.resource_detail::jsonb->>'source_kind'=l.source_kind AND h.resource_detail::jsonb->>'source_id'=l.source_id AND h.state='RESERVED'),0)) THEN RAISE EXCEPTION 'goal lot reserved differs from exact scoped holds' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM income_locations l WHERE l.environment_id=e AND l.user_id=u AND l.assigned_cents::numeric<COALESCE((SELECT sum(h.amount_cents::numeric) FROM native_holds h WHERE h.environment_id=e AND h.user_id=u AND h.resource_kind='ASSIGNED' AND h.resource_id=l.id::text AND h.state='RESERVED'),0)) THEN RAISE EXCEPTION 'assigned income held twice' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM native_goal_balances b WHERE b.environment_id=e AND b.user_id=u AND b.cash_cents::numeric<COALESCE((SELECT sum(h.amount_cents::numeric) FROM native_holds h WHERE h.environment_id=e AND h.user_id=u AND h.resource_kind='GOAL' AND h.resource_id=b.goal_id::text AND h.state='RESERVED'),0)) THEN RAISE EXCEPTION 'goal cash held twice' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM native_goal_lots l WHERE l.environment_id=e AND l.user_id=u AND l.source_kind='INCOME' AND NOT EXISTS(SELECT 1 FROM income_origins o WHERE o.environment_id=e AND o.user_id=u AND o.id::text=l.source_id)) THEN RAISE EXCEPTION 'goal lot missing original income' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM native_goal_lots l WHERE l.environment_id=e AND l.user_id=u AND l.source_kind='PRINCIPAL_RETURN' AND NOT EXISTS(SELECT 1 FROM native_principal_returns p WHERE p.environment_id=e AND p.user_id=u AND p.bank_key=l.source_id)) THEN RAISE EXCEPTION 'goal lot missing actual principal return' USING ERRCODE='23514';END IF;
END $$;
CREATE FUNCTION native_conservation_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e uuid;u uuid;rec record;
BEGIN
 e:=COALESCE(NEW.environment_id,OLD.environment_id);u:=COALESCE(NEW.user_id,OLD.user_id);
 FOR rec IN SELECT id FROM income_origins WHERE environment_id=e AND user_id=u LOOP PERFORM check_one_income(e,u,rec.id);END LOOP;
 PERFORM check_native_resources(e,u);RETURN NULL;
END $$;
CREATE FUNCTION native_terminal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o native_operations%ROWTYPE;
BEGIN
 SELECT * INTO o FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.id;
 IF o.permit_signature IS NOT NULL AND NOT EXISTS(SELECT 1 FROM native_submission_permits p WHERE p.environment_id=o.environment_id AND p.user_id=o.user_id AND p.operation_id=o.id AND p.permit_id=o.permit_id AND p.bank_request_hash=o.bank_request_hash AND p.effect_hash=o.effect_hash AND p.signature_hash=encode(sha256(convert_to(o.permit_signature,'UTF8')),'hex')) THEN RAISE EXCEPTION 'signed native submission lacks exact immutable T2 proof' USING ERRCODE='23514';END IF;
 IF o.status='SETTLED' AND (o.receipt_id IS NULL OR NOT EXISTS(SELECT 1 FROM native_receipts r JOIN native_journals j ON(j.environment_id=r.environment_id AND j.user_id=r.user_id AND j.id=r.journal_id AND j.operation_id=r.operation_id) WHERE r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.id=o.receipt_id AND r.operation_id=o.id AND j.status='POSTED') OR NOT EXISTS(SELECT 1 FROM native_bank_observations b WHERE b.environment_id=o.environment_id AND b.user_id=o.user_id AND b.operation_id=o.id AND b.status='SETTLED')) THEN RAISE EXCEPTION 'native settlement missing original observation journal or receipt' USING ERRCODE='23514';END IF;
 IF o.status IN ('SETTLED','REJECTED','CANCELLED') AND (EXISTS(SELECT 1 FROM native_holds h WHERE h.environment_id=o.environment_id AND h.user_id=o.user_id AND h.operation_id=o.id AND h.state='RESERVED') OR EXISTS(SELECT 1 FROM native_income_reservations r WHERE r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.action_id=o.id AND r.state='RESERVED') OR EXISTS(SELECT 1 FROM native_return_claims r WHERE r.environment_id=o.environment_id AND r.user_id=o.user_id AND r.operation_id=o.id AND r.state='RESERVED')) THEN RAISE EXCEPTION 'terminal native operation cannot retain unresolved resources' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION native_claim_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a uuid;x xid8;
BEGIN
 IF TG_TABLE_NAME='native_income_claims' THEN a:=NEW.action_id;ELSE a:=NEW.operation_id;END IF;
 SELECT accepted_xid INTO x FROM native_operations WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=a;
 IF x IS DISTINCT FROM pg_current_xact_id() THEN RAISE EXCEPTION 'native exact resource set must freeze in original T1 transaction' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION native_resource_terminal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e uuid;u uuid;a uuid;st text;sig text;
BEGIN
 e:=COALESCE(NEW.environment_id,OLD.environment_id);u:=COALESCE(NEW.user_id,OLD.user_id);
 IF TG_TABLE_NAME='native_income_reservations' THEN a:=COALESCE(NEW.action_id,OLD.action_id);ELSE a:=COALESCE(NEW.operation_id,OLD.operation_id);END IF;
 SELECT status,permit_signature INTO st,sig FROM native_operations WHERE environment_id=e AND user_id=u AND id=a;
 IF NEW.state='COMMITTED' AND st<>'SETTLED' OR NEW.state='RELEASED' AND NOT(st='CANCELLED' AND sig IS NULL OR st='REJECTED' AND EXISTS(SELECT 1 FROM native_bank_observations b WHERE b.environment_id=e AND b.user_id=u AND b.operation_id=a AND b.status='REJECTED')) THEN RAISE EXCEPTION 'native resource terminal requires settlement or final absence' USING ERRCODE='23514';END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION native_journal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e uuid;u uuid;j uuid;st text;
BEGIN
 e:=COALESCE(NEW.environment_id,OLD.environment_id);u:=COALESCE(NEW.user_id,OLD.user_id);
 IF TG_TABLE_NAME='native_journals' THEN j:=COALESCE(NEW.id,OLD.id);ELSE j:=COALESCE(NEW.journal_id,OLD.journal_id);END IF;
 SELECT status INTO st FROM native_journals WHERE environment_id=e AND user_id=u AND id=j;
 IF st='POSTED' AND (NOT EXISTS(SELECT 1 FROM native_postings WHERE environment_id=e AND user_id=u AND journal_id=j) OR EXISTS(SELECT 1 FROM native_postings WHERE environment_id=e AND user_id=u AND journal_id=j GROUP BY dimension,currency HAVING sum(CASE direction WHEN 'DEBIT' THEN amount_cents::numeric ELSE -amount_cents::numeric END)<>0 OR sum(CASE direction WHEN 'DEBIT' THEN amount_cents::numeric ELSE 0 END)>9000000000000000)) THEN RAISE EXCEPTION 'native journal must balance independently by dimension and currency' USING ERRCODE='23514';END IF;RETURN NULL;
END $$;
CREATE FUNCTION native_posted_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
 IF TG_TABLE_NAME='native_journals' THEN st:=OLD.status;ELSE SELECT status INTO st FROM native_journals WHERE environment_id=OLD.environment_id AND user_id=OLD.user_id AND id=OLD.journal_id;END IF;
 IF st='POSTED' THEN RAISE EXCEPTION 'posted native journal and legs immutable' USING ERRCODE='23514';END IF;
 IF TG_OP='DELETE' THEN RETURN OLD;END IF;RETURN NEW;
END $$;
CREATE FUNCTION native_posting_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM native_journals WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.journal_id AND status='POSTED') THEN RAISE EXCEPTION 'native legs must precede journal seal' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE FUNCTION native_position_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR ROW(NEW.environment_id,NEW.user_id,NEW.position_id) IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.position_id) OR OLD.closed AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN RAISE EXCEPTION 'native position close is pinned' USING ERRCODE='23514';END IF;
 IF OLD.closing_operation_id IS NOT NULL AND NEW.closing_operation_id IS DISTINCT FROM OLD.closing_operation_id THEN
  IF NEW.closing_operation_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM native_operations o WHERE o.environment_id=OLD.environment_id AND o.user_id=OLD.user_id AND o.id=OLD.closing_operation_id AND (o.status='CANCELLED' AND o.permit_signature IS NULL OR o.status='REJECTED' AND EXISTS(SELECT 1 FROM native_bank_observations b WHERE b.environment_id=o.environment_id AND b.user_id=o.user_id AND b.operation_id=o.id AND b.status='REJECTED'))) THEN RAISE EXCEPTION 'native close can unpin only after final absence' USING ERRCODE='23514';END IF;
 END IF;RETURN NEW;
END $$;
CREATE FUNCTION native_project_v1_settlement() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb;
BEGIN
 IF NEW.status='SETTLED' AND OLD.status<>'SETTLED' AND NEW.command_kind='ALLOCATE_GOAL' THEN
  r:=convert_from(NEW.bank_request,'UTF8')::jsonb;
  IF r->>'kind'='ALLOCATE_GOAL' AND r->>'goal_id' IS NOT NULL THEN
   UPDATE native_goal_balances SET cash_cents=cash_cents+(r->>'amount_cents')::bigint WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND goal_id=(r->>'goal_id')::uuid;
    INSERT INTO native_goal_lots(environment_id,user_id,goal_id,account_id,source_kind,source_id,available_cents)
    VALUES(NEW.environment_id,NEW.user_id,(r->>'goal_id')::uuid,(r->>'source_account_id')::uuid,'INCOME',r->>'income_origin_id',(r->>'amount_cents')::bigint)
    ON CONFLICT(environment_id,user_id,goal_id,account_id,source_kind,source_id) DO UPDATE SET available_cents=native_goal_lots.available_cents+EXCLUDED.available_cents;
  END IF;
 END IF;RETURN NEW;
END $$;
CREATE FUNCTION native_zero_goal_projection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO native_goal_balances(environment_id,user_id,goal_id,cash_cents) VALUES(NEW.environment_id,NEW.user_id,NEW.id,NEW.assigned_cents);RETURN NEW;END $$;
-- +goose StatementEnd

CREATE TRIGGER native_original_operation BEFORE UPDATE OR DELETE ON native_operations FOR EACH ROW EXECUTE FUNCTION protect_native_operation();
CREATE TRIGGER native_identity BEFORE INSERT ON native_operations FOR EACH ROW EXECUTE FUNCTION native_operation_identity();
CREATE TRIGGER v1_native_identity BEFORE INSERT ON operations FOR EACH ROW EXECUTE FUNCTION native_operation_identity();
CREATE TRIGGER native_original_reservation BEFORE UPDATE OR DELETE ON native_income_reservations FOR EACH ROW EXECUTE FUNCTION protect_reservation_original();
CREATE TRIGGER native_original_hold BEFORE UPDATE OR DELETE ON native_holds FOR EACH ROW EXECUTE FUNCTION protect_reservation_original();
CREATE TRIGGER native_original_return_claim BEFORE UPDATE OR DELETE ON native_return_claims FOR EACH ROW EXECUTE FUNCTION protect_reservation_original();
CREATE TRIGGER native_position_transition BEFORE UPDATE OR DELETE ON native_position_states FOR EACH ROW EXECUTE FUNCTION native_position_guard();
CREATE TRIGGER native_immutable_posted_journal BEFORE UPDATE OR DELETE ON native_journals FOR EACH ROW EXECUTE FUNCTION native_posted_immutable();
CREATE TRIGGER native_immutable_posted_leg BEFORE UPDATE OR DELETE ON native_postings FOR EACH ROW EXECUTE FUNCTION native_posted_immutable();
CREATE TRIGGER native_insert_before_seal BEFORE INSERT ON native_postings FOR EACH ROW EXECUTE FUNCTION native_posting_insert_guard();
CREATE CONSTRAINT TRIGGER native_balanced_journal AFTER INSERT OR UPDATE ON native_journals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_journal_guard();
CREATE CONSTRAINT TRIGGER native_balanced_posting AFTER INSERT OR UPDATE OR DELETE ON native_postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_journal_guard();
CREATE CONSTRAINT TRIGGER native_terminal_complete AFTER INSERT OR UPDATE ON native_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_terminal_guard();
CREATE TRIGGER native_income_original_set AFTER INSERT ON native_income_claims FOR EACH ROW EXECUTE FUNCTION native_claim_insert_guard();
CREATE TRIGGER native_hold_original_set AFTER INSERT ON native_holds FOR EACH ROW EXECUTE FUNCTION native_claim_insert_guard();
CREATE TRIGGER native_return_original_set AFTER INSERT ON native_return_claims FOR EACH ROW EXECUTE FUNCTION native_claim_insert_guard();
CREATE CONSTRAINT TRIGGER native_income_terminal AFTER INSERT OR UPDATE ON native_income_reservations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_resource_terminal_guard();
CREATE CONSTRAINT TRIGGER native_hold_terminal AFTER INSERT OR UPDATE ON native_holds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_resource_terminal_guard();
CREATE CONSTRAINT TRIGGER native_return_terminal AFTER INSERT OR UPDATE ON native_return_claims DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_resource_terminal_guard();
-- +goose StatementBegin
DO $$ DECLARE tab text;BEGIN
 FOREACH tab IN ARRAY ARRAY['native_previews','native_confirmations','native_submission_permits','native_income_claims','native_positions','native_principal_returns','native_bank_observations','native_receipts'] LOOP
  EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_immutable_row()',tab||'_immutable',tab);
 END LOOP;
 FOREACH tab IN ARRAY ARRAY['native_income_reservations','native_income_claims','native_holds','native_return_locations','native_return_claims','native_goal_lots','native_goal_balances','accounts','income_locations'] LOOP
  EXECUTE format('CREATE CONSTRAINT TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_conservation_trigger()',tab||'_native_conserved',tab);
 END LOOP;
END $$;
-- +goose StatementEnd

-- New, rebuildable projections of old committed facts; no authorization or
-- bank request is manufactured, and no old row is rewritten.
INSERT INTO native_goal_balances(environment_id,user_id,goal_id,cash_cents) SELECT environment_id,user_id,id,assigned_cents FROM goals;
INSERT INTO native_goal_lots(environment_id,user_id,goal_id,account_id,source_kind,source_id,available_cents)
SELECT o.environment_id,o.user_id,(r.raw->>'goal_id')::uuid,(r.raw->>'source_account_id')::uuid,'INCOME',r.raw->>'income_origin_id',sum((r.raw->>'amount_cents')::bigint)
FROM operations o CROSS JOIN LATERAL (SELECT convert_from(o.bank_request,'UTF8')::jsonb AS raw) r
WHERE o.status='SETTLED' AND r.raw->>'kind'='ALLOCATE_GOAL' GROUP BY o.environment_id,o.user_id,r.raw->>'goal_id',r.raw->>'source_account_id',r.raw->>'income_origin_id';
CREATE TRIGGER native_v1_settlement_projection AFTER UPDATE ON operations FOR EACH ROW EXECUTE FUNCTION native_project_v1_settlement();
CREATE TRIGGER native_new_goal_projection AFTER INSERT ON goals FOR EACH ROW EXECUTE FUNCTION native_zero_goal_projection();

-- +goose Down
-- Explicit forward-only data-bearing migration. Historical originals survive.
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'native execution migration is forward only';END $$;
-- +goose StatementEnd
