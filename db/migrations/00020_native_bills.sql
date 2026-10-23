-- RF023: immutable first observations and independent external bill facts.
-- Final source is frozen before normal Goose preflight; prior failed draft is retained separately.
-- +goose Up
CREATE TABLE native_bill_captures (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,source_id text NOT NULL CHECK(length(source_id) BETWEEN 1 AND 128),
 actor_kind text NOT NULL CHECK(actor_kind IN('USER','SYSTEM')),session_id uuid,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),request_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 4194304),original_hash hash256 NOT NULL,
 recorded_funds_version bigint NOT NULL CHECK(recorded_funds_version>0),observed_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,idempotency_key),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id),
 CHECK((actor_kind='USER')=(session_id IS NOT NULL))
);
CREATE TABLE native_bill_originals (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,source_id text NOT NULL,external_key text NOT NULL CHECK(length(external_key) BETWEEN 1 AND 128),
 statement_account_id uuid NOT NULL,due_date date NOT NULL,issued_at timestamptz NOT NULL,final_total_cents cents NOT NULL CHECK(final_total_cents>0),payee_id text NOT NULL CHECK(length(payee_id) BETWEEN 1 AND 128),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 65536),original_hash hash256 NOT NULL,
 bank_reference_original bytea NOT NULL CHECK(octet_length(bank_reference_original) BETWEEN 1 AND 8192),bank_reference_hash hash256 NOT NULL,
 first_capture_id uuid NOT NULL,first_observed_at timestamptz NOT NULL,materialized_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,source_id,external_key),
 FOREIGN KEY(environment_id,user_id,statement_account_id) REFERENCES accounts(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,first_capture_id) REFERENCES native_bill_captures(environment_id,user_id,id),
 CHECK(issued_at<=first_observed_at AND first_observed_at<=materialized_at)
);
CREATE TABLE native_bill_import_results (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,capture_id uuid NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 262144),original_hash hash256 NOT NULL,
 funds_version bigint NOT NULL CHECK(funds_version>0),source_version bigint NOT NULL CHECK(source_version>0),protection_hash hash256 NOT NULL,
 recorded_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,capture_id),
 FOREIGN KEY(environment_id,user_id,capture_id) REFERENCES native_bill_captures(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,source_version) REFERENCES protection_snapshots(environment_id,user_id,version)
);
CREATE TABLE native_bill_capture_resolutions (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,capture_id uuid NOT NULL,result_capture_id uuid NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 8192),original_hash hash256 NOT NULL,recorded_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,capture_id),
 FOREIGN KEY(environment_id,user_id,capture_id) REFERENCES native_bill_captures(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,result_capture_id) REFERENCES native_bill_import_results(environment_id,user_id,capture_id) DEFERRABLE INITIALLY DEFERRED
);
-- +goose StatementBegin
CREATE FUNCTION native_bill_capture_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;namespace text;fv bigint;last_observation timestamptz;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash THEN RAISE EXCEPTION 'bill capture raw hash differs' USING ERRCODE='23514';END IF;
 raw:=convert_from(NEW.original,'UTF8')::jsonb;
 SELECT e.namespace,u.funds_version INTO STRICT namespace,fv FROM environments e JOIN users u ON(u.environment_id=e.id) WHERE e.id=NEW.environment_id AND u.id=NEW.user_id;
 SELECT max(observed_at) INTO last_observation FROM native_bill_captures WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id;
 IF raw->>'protocol' IS DISTINCT FROM 'zhiyu-external-bill-capture-v1' OR raw->>'id' IS DISTINCT FROM NEW.id::text OR raw->'scope' IS DISTINCT FROM jsonb_build_object('environment_id',NEW.environment_id::text,'user_id',NEW.user_id::text) OR
 raw->>'source_id' IS DISTINCT FROM NEW.source_id OR raw->>'actor_kind' IS DISTINCT FROM NEW.actor_kind OR raw->>'session_id' IS DISTINCT FROM coalesce(NEW.session_id::text,'00000000-0000-0000-0000-000000000000') OR
 raw->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key OR raw->>'request_hash' IS DISTINCT FROM NEW.request_hash::text OR raw->'bank_authority' IS DISTINCT FROM 'false'::jsonb OR (raw->>'observed_at')::timestamptz IS DISTINCT FROM NEW.observed_at OR
 (raw->>'recorded_funds_version')::bigint IS DISTINCT FROM NEW.recorded_funds_version OR fv<>NEW.recorded_funds_version OR (raw->'basis'->>'funds_version')::bigint+1 IS DISTINCT FROM NEW.recorded_funds_version OR
 raw->'statements'->>'protocol' IS DISTINCT FROM 'zhiyu-external-bill-capture-v1/statements' OR raw->'statements'->>'namespace' IS DISTINCT FROM namespace OR raw->'statements'->>'user_id' IS DISTINCT FROM NEW.user_id::text OR raw->'statements'->>'source_id' IS DISTINCT FROM NEW.source_id OR raw->'statements'->'complete' IS DISTINCT FROM 'true'::jsonb OR
 raw->'bank'->>'protocol' IS DISTINCT FROM 'zhiyu-bank-v2/liabilities' OR raw->'bank'->>'namespace' IS DISTINCT FROM namespace OR raw->'bank'->>'user_id' IS DISTINCT FROM NEW.user_id::text OR raw->'bank'->'complete' IS DISTINCT FROM 'true'::jsonb OR jsonb_typeof(raw->'original_hashes') IS DISTINCT FROM 'object' OR NEW.observed_at<last_observation THEN
 RAISE EXCEPTION 'bill capture scope or first observation differs' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE FUNCTION native_bill_original_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;bank jsonb;capture jsonb;earliest timestamptz;namespace text;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash OR encode(sha256(NEW.bank_reference_original),'hex')<>NEW.bank_reference_hash THEN RAISE EXCEPTION 'bill original raw hash differs' USING ERRCODE='23514';END IF;
 raw:=convert_from(NEW.original,'UTF8')::jsonb;bank:=convert_from(NEW.bank_reference_original,'UTF8')::jsonb;
 SELECT e.namespace,convert_from(c.original,'UTF8')::jsonb INTO STRICT namespace,capture FROM native_bill_captures c JOIN environments e ON(e.id=c.environment_id) WHERE c.environment_id=NEW.environment_id AND c.user_id=NEW.user_id AND c.id=NEW.first_capture_id;
 SELECT min(observed_at) INTO earliest FROM native_bill_captures WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND source_id=NEW.source_id AND convert_from(original,'UTF8')::jsonb->'original_hashes'->>NEW.id::text=NEW.original_hash::text;
 IF raw->>'protocol' IS DISTINCT FROM 'zhiyu-external-bill-v1' OR raw->>'id' IS DISTINCT FROM NEW.id::text OR raw->'scope' IS DISTINCT FROM jsonb_build_object('environment_id',NEW.environment_id::text,'user_id',NEW.user_id::text) OR raw->>'bank_namespace' IS DISTINCT FROM namespace OR
 raw->>'statement_account_id' IS DISTINCT FROM NEW.statement_account_id::text OR raw->>'due_date' IS DISTINCT FROM NEW.due_date::text OR (raw->>'issued_at')::timestamptz IS DISTINCT FROM NEW.issued_at OR (raw->>'final_total_cents')::bigint IS DISTINCT FROM NEW.final_total_cents OR raw->>'payee_id' IS DISTINCT FROM NEW.payee_id OR raw->>'external_key' IS DISTINCT FROM NEW.external_key OR
 bank->>'id' IS DISTINCT FROM 'bill:'||NEW.id::text OR bank->>'kind' IS DISTINCT FROM 'BILL' OR bank->>'policy_id' IS DISTINCT FROM '' OR bank->>'period' IS DISTINCT FROM '' OR (bank->>'final_total_cents')::bigint IS DISTINCT FROM NEW.final_total_cents OR bank->>'payee_id' IS DISTINCT FROM NEW.payee_id OR bank->>'evidence_hash' IS DISTINCT FROM NEW.original_hash::text OR
 capture->>'source_id' IS DISTINCT FROM NEW.source_id OR capture->'original_hashes'->>NEW.id::text IS DISTINCT FROM NEW.original_hash::text OR (capture->>'observed_at')::timestamptz IS DISTINCT FROM NEW.first_observed_at OR earliest IS DISTINCT FROM NEW.first_observed_at THEN
 RAISE EXCEPTION 'bill original or earliest real capture differs' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE FUNCTION native_bill_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;protection hash256;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash THEN RAISE EXCEPTION 'bill result raw hash differs' USING ERRCODE='23514';END IF;raw:=convert_from(NEW.original,'UTF8')::jsonb;
 SELECT original_hash INTO STRICT protection FROM protection_snapshots WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND version=NEW.source_version;
 IF raw->>'protocol' IS DISTINCT FROM 'native-bill-import-result-v1' OR raw->>'capture_id' IS DISTINCT FROM NEW.capture_id::text OR raw->'scope' IS DISTINCT FROM jsonb_build_object('environment_id',NEW.environment_id::text,'user_id',NEW.user_id::text) OR
 (raw->>'funds_version')::bigint IS DISTINCT FROM NEW.funds_version OR (raw->>'source_version')::bigint IS DISTINCT FROM NEW.source_version OR raw->>'protection_hash' IS DISTINCT FROM NEW.protection_hash::text OR protection<>NEW.protection_hash OR raw->'bank_authority' IS DISTINCT FROM 'false'::jsonb OR (raw->>'recorded_at')::timestamptz IS DISTINCT FROM NEW.recorded_at THEN
 RAISE EXCEPTION 'bill import result differs' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE FUNCTION native_bill_resolution_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;prior jsonb;current_capture jsonb;item record;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash THEN RAISE EXCEPTION 'bill resolution hash differs' USING ERRCODE='23514';END IF;raw:=convert_from(NEW.original,'UTF8')::jsonb;
 SELECT convert_from(original,'UTF8')::jsonb INTO STRICT prior FROM native_bill_captures WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.capture_id;
 SELECT convert_from(original,'UTF8')::jsonb INTO STRICT current_capture FROM native_bill_captures WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.result_capture_id;
 IF raw->>'protocol' IS DISTINCT FROM 'native-bill-capture-resolution-v1' OR raw->>'capture_id' IS DISTINCT FROM NEW.capture_id::text OR raw->>'result_capture_id' IS DISTINCT FROM NEW.result_capture_id::text OR raw->'scope' IS DISTINCT FROM jsonb_build_object('environment_id',NEW.environment_id::text,'user_id',NEW.user_id::text) OR
 prior->>'source_id' IS DISTINCT FROM current_capture->>'source_id' OR (prior->>'observed_at')::timestamptz>(current_capture->>'observed_at')::timestamptz OR (raw->>'recorded_at')::timestamptz IS DISTINCT FROM NEW.recorded_at THEN RAISE EXCEPTION 'bill resolution scope differs' USING ERRCODE='23514';END IF;
 FOR item IN SELECT key,value FROM jsonb_each_text(prior->'original_hashes') LOOP
 IF current_capture->'original_hashes'->>item.key IS DISTINCT FROM item.value THEN RAISE EXCEPTION 'bill capture cannot be resolved by substituted statement' USING ERRCODE='23514';END IF;
 END LOOP;RETURN NEW;
END $$;
CREATE FUNCTION native_bill_import_closure_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE result native_bill_import_results;raw jsonb;capture jsonb;input jsonb;declared jsonb;actual jsonb;item record;bill native_bill_originals;paid numeric;fv bigint;sv bigint;
BEGIN
 SELECT * INTO STRICT result FROM native_bill_import_results WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND capture_id=NEW.capture_id;
 raw:=convert_from(result.original,'UTF8')::jsonb;
 SELECT convert_from(original,'UTF8')::jsonb INTO STRICT capture FROM native_bill_captures WHERE environment_id=result.environment_id AND user_id=result.user_id AND id=result.capture_id;
 SELECT funds_version,source_version INTO STRICT fv,sv FROM users WHERE environment_id=result.environment_id AND id=result.user_id;
 SELECT convert_from(input_original,'UTF8')::jsonb INTO input FROM template_protection_publications WHERE environment_id=result.environment_id AND user_id=result.user_id AND source_version=result.source_version AND protection_hash=result.protection_hash;
 IF fv<>result.funds_version OR sv<>result.source_version OR input IS NULL OR input->'scope' IS DISTINCT FROM raw->'scope' OR
 jsonb_typeof(raw->'resolved_capture_ids') IS DISTINCT FROM 'array' OR jsonb_typeof(raw->'imported_bill_ids') IS DISTINCT FROM 'array' OR
 jsonb_array_length(raw->'resolved_capture_ids') NOT BETWEEN 1 AND 1000 OR jsonb_array_length(raw->'imported_bill_ids')>1000 OR NOT(raw->'resolved_capture_ids' @> jsonb_build_array(result.capture_id::text)) THEN
 RAISE EXCEPTION 'bill result lacks current complete protection or bounded closure' USING ERRCODE='23514';END IF;
 SELECT coalesce(jsonb_agg(value ORDER BY value),'[]'::jsonb) INTO declared FROM jsonb_array_elements_text(raw->'resolved_capture_ids');
 SELECT coalesce(jsonb_agg(capture_id::text ORDER BY capture_id::text),'[]'::jsonb) INTO actual FROM native_bill_capture_resolutions WHERE environment_id=result.environment_id AND user_id=result.user_id AND result_capture_id=result.capture_id;
 IF declared IS DISTINCT FROM actual THEN RAISE EXCEPTION 'bill result resolution membership differs' USING ERRCODE='23514';END IF;
 IF (SELECT count(*) FROM jsonb_array_elements_text(raw->'imported_bill_ids'))<>(SELECT count(DISTINCT value) FROM jsonb_array_elements_text(raw->'imported_bill_ids')) THEN
 RAISE EXCEPTION 'duplicate imported bill identity' USING ERRCODE='23514';END IF;
 FOR item IN SELECT value FROM jsonb_array_elements_text(raw->'imported_bill_ids') LOOP
 IF NOT EXISTS(SELECT 1 FROM native_bill_originals WHERE environment_id=result.environment_id AND user_id=result.user_id AND id::text=item.value AND materialized_at=result.recorded_at) THEN RAISE EXCEPTION 'declared imported bill absent' USING ERRCODE='23514';END IF;
 END LOOP;
 IF (SELECT count(*) FROM native_bill_originals WHERE environment_id=result.environment_id AND user_id=result.user_id AND source_id=capture->>'source_id')<>(SELECT count(*) FROM jsonb_each(capture->'original_hashes')) THEN
 RAISE EXCEPTION 'bill result incomplete original inventory' USING ERRCODE='23514';END IF;
 FOR item IN SELECT key,value FROM jsonb_each_text(capture->'original_hashes') LOOP
 SELECT * INTO bill FROM native_bill_originals WHERE environment_id=result.environment_id AND user_id=result.user_id AND id::text=item.key AND source_id=capture->>'source_id' AND original_hash::text=item.value;
 IF NOT FOUND THEN RAISE EXCEPTION 'bill result omitted immutable original' USING ERRCODE='23514';END IF;
 SELECT coalesce(sum(amount_cents),0) INTO paid FROM native_obligation_payments WHERE environment_id=result.environment_id AND user_id=result.user_id AND obligation_id='bill:'||bill.id::text;
 IF paid>bill.final_total_cents OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(input->'facts'->'bills') fact WHERE fact->>'id'=bill.id::text AND fact->>'account_id'=bill.statement_account_id::text AND fact->>'due_date'=bill.due_date::text AND (fact->>'total_cents')::numeric=bill.final_total_cents AND (fact->>'paid_cents')::numeric=paid AND fact->'verified'='true'::jsonb) OR
 NOT EXISTS(SELECT 1 FROM jsonb_array_elements(input->'external_liabilities') liability WHERE liability->>'id'='bill:'||bill.id::text AND liability->>'due_date'=bill.due_date::text AND (liability->>'remaining_cents')::numeric=bill.final_total_cents-paid AND liability->>'source_hash'=bill.original_hash::text AND liability->'verified'='true'::jsonb) THEN
 RAISE EXCEPTION 'bill result protection excludes original unpaid debt' USING ERRCODE='23514';END IF;
 END LOOP;RETURN NEW;
END $$;
-- +goose StatementEnd
CREATE TRIGGER native_bill_capture_original BEFORE INSERT ON native_bill_captures FOR EACH ROW EXECUTE FUNCTION native_bill_capture_guard();
CREATE TRIGGER native_bill_capture_immutable BEFORE UPDATE OR DELETE ON native_bill_captures FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_bill_original BEFORE INSERT ON native_bill_originals FOR EACH ROW EXECUTE FUNCTION native_bill_original_guard();
CREATE TRIGGER native_bill_original_immutable BEFORE UPDATE OR DELETE ON native_bill_originals FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER native_bill_result_original BEFORE INSERT ON native_bill_import_results FOR EACH ROW EXECUTE FUNCTION native_bill_result_guard();
CREATE TRIGGER native_bill_result_immutable BEFORE UPDATE OR DELETE ON native_bill_import_results FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE CONSTRAINT TRIGGER native_bill_import_complete AFTER INSERT ON native_bill_import_results DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION native_bill_import_closure_guard();
CREATE TRIGGER native_bill_resolution_original BEFORE INSERT ON native_bill_capture_resolutions FOR EACH ROW EXECUTE FUNCTION native_bill_resolution_guard();
CREATE TRIGGER native_bill_resolution_immutable BEFORE UPDATE OR DELETE ON native_bill_capture_resolutions FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
REVOKE ALL ON native_bill_captures,native_bill_originals,native_bill_import_results,native_bill_capture_resolutions FROM zygo_app;
GRANT SELECT,INSERT ON native_bill_captures,native_bill_originals,native_bill_import_results,native_bill_capture_resolutions TO zygo_app;
-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'external bill original observations are forward only';END $$;
-- +goose StatementEnd
