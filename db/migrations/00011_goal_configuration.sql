-- +goose Up
CREATE TABLE goal_configuration_events (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,goal_id uuid NOT NULL,goal_version bigint NOT NULL CHECK(goal_version>0),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 16 AND 128),
 request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 4096),request_hash hash256 NOT NULL,
 session_id uuid NOT NULL,action text NOT NULL CHECK(action IN ('CREATE','UPDATE')),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 16384),original_hash hash256 NOT NULL,
 confirmed_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,goal_id,goal_version),UNIQUE(environment_id,user_id,idempotency_key),
 FOREIGN KEY(environment_id,user_id,goal_id) REFERENCES goals(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id)
);
CREATE TRIGGER immutable_goal_configuration BEFORE UPDATE OR DELETE ON goal_configuration_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
-- +goose Down
DROP TABLE goal_configuration_events;
