-- Frozen after isolated actual PG preflight; immutable question originals.
-- +goose Up
CREATE TABLE native_priority_analyses (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,session_id uuid NOT NULL,plan_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 16 AND 128),
 request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 16384),request_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 16777216),original_hash hash256 NOT NULL,
 question_id uuid,created_at timestamptz NOT NULL,expires_at timestamptz NOT NULL CHECK(expires_at>created_at),
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,idempotency_key),
 UNIQUE(environment_id,user_id,question_id),UNIQUE(environment_id,user_id,id,question_id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,plan_id) REFERENCES native_joint_plans(environment_id,user_id,id)
);
CREATE TABLE native_priority_answers (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,session_id uuid NOT NULL,
 analysis_id uuid NOT NULL,question_id uuid NOT NULL,idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 16 AND 128),
 request bytea NOT NULL CHECK(octet_length(request) BETWEEN 1 AND 16384),request_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 131072),original_hash hash256 NOT NULL,answered_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,idempotency_key),UNIQUE(environment_id,user_id,question_id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,analysis_id,question_id) REFERENCES native_priority_analyses(environment_id,user_id,id,question_id)
);
-- +goose StatementBegin
CREATE FUNCTION native_question_original_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb; req jsonb;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash OR encode(sha256(NEW.request),'hex')<>NEW.request_hash THEN
  RAISE EXCEPTION 'native question original hash differs' USING ERRCODE='23514';END IF;
 raw:=convert_from(NEW.original,'UTF8')::jsonb;req:=convert_from(NEW.request,'UTF8')::jsonb;
 IF raw->>'id' IS DISTINCT FROM NEW.id::text OR raw->>'session_id' IS DISTINCT FROM NEW.session_id::text OR
  raw->'request' IS DISTINCT FROM req OR raw->'bank_authority' IS DISTINCT FROM 'false'::jsonb THEN
  RAISE EXCEPTION 'native question original identity differs' USING ERRCODE='23514';END IF;
 IF TG_TABLE_NAME='native_priority_analyses' THEN
  IF raw->>'protocol' IS DISTINCT FROM 'native-priority-analysis-v1' OR raw#>>'{input,Scope,environment_id}' IS DISTINCT FROM NEW.environment_id::text OR
   raw#>>'{input,Scope,user_id}' IS DISTINCT FROM NEW.user_id::text OR req->>'plan_id' IS DISTINCT FROM NEW.plan_id::text OR
   raw#>>'{result,question,original_question_id}' IS DISTINCT FROM NEW.question_id::text OR raw#>'{result,bank_authority}' IS DISTINCT FROM 'false'::jsonb THEN
   RAISE EXCEPTION 'native analysis original scope or question differs' USING ERRCODE='23514';END IF;
 ELSE
  IF raw->>'analysis_id' IS DISTINCT FROM NEW.analysis_id::text OR raw#>>'{preference,original_question_id}' IS DISTINCT FROM NEW.question_id::text OR
   raw#>>'{preference,answer_id}' IS DISTINCT FROM NEW.id::text OR raw#>'{preference,reevaluate_required}' IS DISTINCT FROM 'true'::jsonb OR
   raw#>'{preference,bank_authority}' IS DISTINCT FROM 'false'::jsonb THEN
   RAISE EXCEPTION 'native answer cannot become financial confirmation' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER native_analysis_original BEFORE INSERT ON native_priority_analyses FOR EACH ROW EXECUTE FUNCTION native_question_original_guard();
CREATE TRIGGER native_answer_original BEFORE INSERT ON native_priority_answers FOR EACH ROW EXECUTE FUNCTION native_question_original_guard();
CREATE TRIGGER native_analysis_immutable BEFORE UPDATE OR DELETE ON native_priority_analyses FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_answer_immutable BEFORE UPDATE OR DELETE ON native_priority_answers FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'native question originals are forward only';END $$;
-- +goose StatementEnd
