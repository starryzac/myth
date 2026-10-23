-- +goose Up
CREATE TABLE standing_consumers (
 environment_id uuid NOT NULL REFERENCES environments(id), consumer_name text NOT NULL,
 paused boolean NOT NULL DEFAULT false, version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(environment_id,consumer_name)
);
CREATE TABLE standing_event_receipts (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, consumer_name text NOT NULL, event_id uuid NOT NULL,
 event_original bytea NOT NULL, event_hash hash256 NOT NULL, outcome text NOT NULL CHECK(outcome IN ('REGISTERED','UNSUPPORTED','INVALID')),
 reason text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment_id,user_id,consumer_name,event_id),
 FOREIGN KEY(environment_id,user_id,event_id) REFERENCES outbox(environment_id,user_id,id),
 FOREIGN KEY(environment_id,consumer_name) REFERENCES standing_consumers(environment_id,consumer_name)
);
CREATE TABLE standing_inbox (
 environment_id uuid NOT NULL, user_id uuid NOT NULL, consumer_name text NOT NULL, event_id uuid NOT NULL,
 origin_id uuid NOT NULL, policy_id uuid NOT NULL, policy_version bigint NOT NULL, grant_id uuid NOT NULL, grant_version bigint NOT NULL,
 family text NOT NULL CHECK(family='ALLOCATE_GOAL'), accept_key text NOT NULL CHECK(length(accept_key) BETWEEN 16 AND 128),
 plan_id uuid, operation_id uuid, state text NOT NULL DEFAULT 'READY' CHECK(state IN ('READY','RUNNING','ACCEPTED','NO_ACTION','BLOCKED','EXHAUSTED')),
 claim_generation bigint NOT NULL DEFAULT 0 CHECK(claim_generation>=0), lease_owner text, lease_until timestamptz,
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(), attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 max_attempts integer NOT NULL DEFAULT 8 CHECK(max_attempts>0 AND max_attempts<=32), paused boolean NOT NULL DEFAULT false,
 last_error text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
 PRIMARY KEY(environment_id,user_id,consumer_name,event_id,policy_id,policy_version,grant_id,grant_version,family),
 UNIQUE(environment_id,user_id,accept_key),
 UNIQUE(environment_id,user_id,consumer_name,origin_id,policy_id,policy_version,grant_id,grant_version,family),
 FOREIGN KEY(environment_id,user_id,consumer_name,event_id) REFERENCES standing_event_receipts(environment_id,user_id,consumer_name,event_id),
 FOREIGN KEY(environment_id,user_id,origin_id) REFERENCES income_origins(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,policy_id,policy_version) REFERENCES policies(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,grant_id,grant_version) REFERENCES grants(environment_id,user_id,id,version),
 FOREIGN KEY(environment_id,user_id,plan_id) REFERENCES plans(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,operation_id) REFERENCES operations(environment_id,user_id,id),
 CHECK((lease_owner IS NULL)=(lease_until IS NULL))
);
CREATE INDEX standing_ready ON standing_inbox(environment_id,consumer_name,next_attempt_at,event_id) WHERE state IN ('READY','RUNNING') AND NOT paused;

-- +goose StatementBegin
CREATE FUNCTION protect_standing_inbox() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR ROW(NEW.environment_id,NEW.user_id,NEW.consumer_name,NEW.event_id,NEW.origin_id,NEW.policy_id,NEW.policy_version,NEW.grant_id,NEW.grant_version,NEW.family,NEW.accept_key,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.environment_id,OLD.user_id,OLD.consumer_name,OLD.event_id,OLD.origin_id,OLD.policy_id,OLD.policy_version,OLD.grant_id,OLD.grant_version,OLD.family,OLD.accept_key,OLD.created_at) THEN RAISE EXCEPTION 'immutable standing action identity' USING ERRCODE='23514'; END IF;
 IF OLD.plan_id IS NOT NULL AND NEW.plan_id IS DISTINCT FROM OLD.plan_id THEN RAISE EXCEPTION 'standing plan can only be pinned once' USING ERRCODE='23514'; END IF;
 IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN RAISE EXCEPTION 'standing original operation cannot be replaced' USING ERRCODE='23514'; END IF;
 IF NEW.claim_generation<OLD.claim_generation THEN RAISE EXCEPTION 'standing claim generation cannot move backwards' USING ERRCODE='23514'; END IF;
 IF OLD.state IN ('ACCEPTED','NO_ACTION','BLOCKED','EXHAUSTED') AND NEW.state<>OLD.state THEN RAISE EXCEPTION 'terminal standing result cannot be silently requeued' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER immutable_standing_receipt BEFORE UPDATE OR DELETE ON standing_event_receipts FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER immutable_standing_identity BEFORE UPDATE OR DELETE ON standing_inbox FOR EACH ROW EXECUTE FUNCTION protect_standing_inbox();

-- +goose Down
DROP TABLE standing_inbox,standing_event_receipts,standing_consumers;
DROP FUNCTION protect_standing_inbox();
