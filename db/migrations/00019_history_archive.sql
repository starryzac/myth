-- Frozen passive evidence only. No authority or financial takeover is created.
-- +goose Up
CREATE TABLE history_archives (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,session_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),request_hash hash256 NOT NULL,
 mode text NOT NULL CHECK(mode IN ('SUPPLIED_HISTORY','AUTHENTICATED_CURRENT_READSET')),
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 8388608),original_hash hash256 NOT NULL,
 manifest_original bytea NOT NULL CHECK(octet_length(manifest_original) BETWEEN 1 AND 4194304),manifest_hash hash256 NOT NULL,root_hash hash256 NOT NULL,
 wire_original bytea NOT NULL CHECK(octet_length(wire_original) BETWEEN 1 AND 100663296),wire_hash hash256 NOT NULL,
 object_count integer NOT NULL CHECK(object_count BETWEEN 0 AND 10000),total_bytes bigint NOT NULL CHECK(total_bytes BETWEEN 0 AND 67108864),
 stored_count integer NOT NULL CHECK(stored_count BETWEEN 0 AND object_count),stored_bytes bigint NOT NULL CHECK(stored_bytes BETWEEN 0 AND total_bytes),
 verification_status text NOT NULL CHECK(verification_status IN ('VALID','INVALID','INCOMPLETE','UNSUPPORTED_VERSION')),
 created_at timestamptz NOT NULL,
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,idempotency_key),
 FOREIGN KEY(environment_id,user_id) REFERENCES users(environment_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id)
);
CREATE TABLE history_archive_objects (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,archive_id uuid NOT NULL,id hash256 NOT NULL,ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 10000),
 source_table text NOT NULL,row_key text NOT NULL CHECK(length(row_key) BETWEEN 1 AND 4096),representation text NOT NULL CHECK(representation IN ('captured_wire','legacy_canonical','db_export','stored_original')),
 protocol text NOT NULL CHECK(length(protocol) BETWEEN 1 AND 160),
 metadata_original bytea NOT NULL CHECK(octet_length(metadata_original) BETWEEN 1 AND 32768),metadata_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 16777216),original_hash hash256 NOT NULL,
 PRIMARY KEY(environment_id,user_id,archive_id,id),UNIQUE(environment_id,user_id,archive_id,ordinal),
 UNIQUE(environment_id,user_id,archive_id,source_table,row_key,representation),
 FOREIGN KEY(environment_id,user_id,archive_id) REFERENCES history_archives(environment_id,user_id,id)
);
CREATE TABLE history_imports (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,id uuid NOT NULL,archive_id uuid NOT NULL,session_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),request_hash hash256 NOT NULL,
 original bytea NOT NULL CHECK(octet_length(original) BETWEEN 1 AND 8192),original_hash hash256 NOT NULL,
 mapping_original bytea NOT NULL CHECK(octet_length(mapping_original) BETWEEN 1 AND 4194304),mapping_hash hash256 NOT NULL,reviewed_hash hash256 NOT NULL,
 source_status text NOT NULL CHECK(source_status IN ('VALID','INVALID','INCOMPLETE','UNSUPPORTED_VERSION')),
 state text NOT NULL CHECK(state IN ('READY','RUNNING','COMPLETED')),paused boolean NOT NULL DEFAULT false,
 cursor integer NOT NULL DEFAULT 0 CHECK(cursor BETWEEN 0 AND 10000),generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0),attempts bigint NOT NULL DEFAULT 0 CHECK(attempts>=0),
 created_at timestamptz NOT NULL,updated_at timestamptz NOT NULL,activated boolean NOT NULL DEFAULT false CHECK(NOT activated),
 PRIMARY KEY(environment_id,user_id,id),UNIQUE(environment_id,user_id,idempotency_key),UNIQUE(environment_id,user_id,id,archive_id),
 FOREIGN KEY(environment_id,user_id,archive_id) REFERENCES history_archives(environment_id,user_id,id),
 FOREIGN KEY(environment_id,user_id,session_id) REFERENCES sessions(environment_id,user_id,id)
);
CREATE TABLE history_passive_rows (
 environment_id uuid NOT NULL,user_id uuid NOT NULL,import_id uuid NOT NULL,archive_id uuid NOT NULL,object_id hash256 NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 10000),destination_id text NOT NULL CHECK(length(destination_id) BETWEEN 1 AND 128),source_raw_hash hash256 NOT NULL,
 PRIMARY KEY(environment_id,user_id,import_id,object_id),UNIQUE(environment_id,user_id,import_id,ordinal),UNIQUE(environment_id,user_id,import_id,destination_id),
 FOREIGN KEY(environment_id,user_id,import_id,archive_id) REFERENCES history_imports(environment_id,user_id,id,archive_id),
 FOREIGN KEY(environment_id,user_id,archive_id,object_id) REFERENCES history_archive_objects(environment_id,user_id,archive_id,id)
);
-- +goose StatementBegin
CREATE FUNCTION history_archive_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;manifest jsonb;wire jsonb;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash OR encode(sha256(NEW.manifest_original),'hex')<>NEW.manifest_hash OR encode(sha256(NEW.wire_original),'hex')<>NEW.wire_hash THEN RAISE EXCEPTION 'history archive raw hash differs' USING ERRCODE='23514';END IF;
 raw:=convert_from(NEW.original,'UTF8')::jsonb;manifest:=convert_from(NEW.manifest_original,'UTF8')::jsonb;wire:=convert_from(NEW.wire_original,'UTF8')::jsonb;
 IF raw->>'protocol' IS DISTINCT FROM 'zhiyu-history-record-v1' OR raw->>'id' IS DISTINCT FROM NEW.id::text OR raw->'scope' IS DISTINCT FROM jsonb_build_object('environment_id',NEW.environment_id::text,'user_id',NEW.user_id::text) OR
 raw->>'session_id' IS DISTINCT FROM NEW.session_id::text OR raw->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key OR raw->>'request_hash' IS DISTINCT FROM NEW.request_hash::text OR raw->>'mode' IS DISTINCT FROM NEW.mode OR
 raw->>'wire_hash' IS DISTINCT FROM NEW.wire_hash::text OR raw->'manifest' IS DISTINCT FROM manifest OR wire->'manifest' IS DISTINCT FROM manifest OR manifest->>'protocol' IS DISTINCT FROM 'zhiyu-history-archive-v1' OR
 manifest->>'root_hash' IS DISTINCT FROM NEW.root_hash::text OR jsonb_typeof(manifest->'objects') IS DISTINCT FROM 'array' OR jsonb_array_length(manifest->'objects') IS DISTINCT FROM NEW.object_count OR
 (manifest->>'total_bytes')::bigint IS DISTINCT FROM NEW.total_bytes OR (raw->>'stored_count')::integer IS DISTINCT FROM NEW.stored_count OR (raw->>'stored_bytes')::bigint IS DISTINCT FROM NEW.stored_bytes OR jsonb_array_length(wire->'objects') IS DISTINCT FROM NEW.stored_count OR raw->'verification'->>'status' IS DISTINCT FROM NEW.verification_status OR raw->'verification'->>'root_hash' IS DISTINCT FROM NEW.root_hash::text OR
 raw->'bank_authority' IS DISTINCT FROM 'false'::jsonb OR raw->'activated' IS DISTINCT FROM 'false'::jsonb OR raw->'verification'->'bank_authority' IS DISTINCT FROM 'false'::jsonb OR raw->'verification'->'activated' IS DISTINCT FROM 'false'::jsonb OR
 (raw->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at THEN RAISE EXCEPTION 'history archive header differs' USING ERRCODE='23514';END IF;
 IF (NEW.mode='SUPPLIED_HISTORY' AND (NEW.request_hash<>NEW.wire_hash OR raw->>'source_trust' IS DISTINCT FROM 'DECLARED_UNVERIFIED')) OR
 (NEW.mode='AUTHENTICATED_CURRENT_READSET' AND (raw->>'source_trust' IS DISTINCT FROM 'LOCAL_AUTHENTICATED_READSET' OR manifest->'source'->>'environment_id' IS DISTINCT FROM NEW.environment_id::text OR manifest->'source'->>'user_id' IS DISTINCT FROM NEW.user_id::text)) THEN
 RAISE EXCEPTION 'history archive source mode differs' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION history_object_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE metadata jsonb;manifest jsonb;expected jsonb;wire jsonb;wire_member jsonb;
BEGIN
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash OR encode(sha256(NEW.metadata_original),'hex')<>NEW.metadata_hash THEN RAISE EXCEPTION 'history original hash differs' USING ERRCODE='23514';END IF;
 metadata:=convert_from(NEW.metadata_original,'UTF8')::jsonb;
 SELECT convert_from(manifest_original,'UTF8')::jsonb,convert_from(wire_original,'UTF8')::jsonb INTO STRICT manifest,wire FROM history_archives WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.archive_id;
 SELECT value INTO STRICT wire_member FROM jsonb_array_elements(wire->'objects') WHERE value->>'id'=NEW.id::text;
 expected:=manifest->'objects'->(NEW.ordinal-1);
 IF metadata IS DISTINCT FROM expected OR metadata->>'id' IS DISTINCT FROM NEW.id::text OR metadata->>'table' IS DISTINCT FROM NEW.source_table OR metadata->>'row_key' IS DISTINCT FROM NEW.row_key OR
 metadata->>'representation' IS DISTINCT FROM NEW.representation OR metadata->>'protocol' IS DISTINCT FROM NEW.protocol OR metadata->>'raw_hash' IS DISTINCT FROM NEW.original_hash::text OR (metadata->>'bytes')::integer IS DISTINCT FROM octet_length(NEW.original) OR
 (wire_member-'original') IS DISTINCT FROM metadata OR decode(wire_member->>'original','base64') IS DISTINCT FROM NEW.original THEN
 RAISE EXCEPTION 'history member identity differs' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION history_archive_closure() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE aid uuid;expected integer;total bigint;actual integer;actual_bytes bigint;
BEGIN
 IF TG_TABLE_NAME='history_archives' THEN aid:=NEW.id;ELSE aid:=NEW.archive_id;END IF;
 SELECT stored_count,stored_bytes INTO STRICT expected,total FROM history_archives WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=aid;
 SELECT count(*),coalesce(sum(octet_length(original)),0) INTO actual,actual_bytes FROM history_archive_objects WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND archive_id=aid;
 IF actual<>expected OR actual_bytes<>total THEN RAISE EXCEPTION 'history archive members incomplete' USING ERRCODE='23514';END IF;RETURN NULL;
END $$;
CREATE FUNCTION history_import_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE raw jsonb;mapping jsonb;manifest jsonb;entry jsonb;expected jsonb;i integer;source_state text;
BEGIN
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['state','paused','cursor','generation','attempts','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','paused','cursor','generation','attempts','updated_at']) OR NEW.cursor<OLD.cursor OR NEW.generation<OLD.generation OR NEW.attempts<OLD.attempts OR NEW.updated_at<OLD.updated_at OR
  (OLD.state='COMPLETED' AND NEW IS DISTINCT FROM OLD) OR (NEW.cursor>OLD.cursor AND (NEW.generation<>OLD.generation+1 OR NEW.attempts<>OLD.attempts+1 OR OLD.paused)) THEN RAISE EXCEPTION 'history import checkpoint identity or transition differs' USING ERRCODE='23514';END IF;
  RETURN NEW;
 END IF;
 IF encode(sha256(NEW.original),'hex')<>NEW.original_hash OR encode(sha256(NEW.mapping_original),'hex')<>NEW.mapping_hash OR encode(sha256(convert_to('zhiyu-passive-history-mapping-v1','UTF8')||decode('00','hex')||NEW.mapping_original),'hex')<>NEW.reviewed_hash THEN RAISE EXCEPTION 'history mapping hash differs' USING ERRCODE='23514';END IF;
 raw:=convert_from(NEW.original,'UTF8')::jsonb;mapping:=convert_from(NEW.mapping_original,'UTF8')::jsonb;
 SELECT convert_from(manifest_original,'UTF8')::jsonb,verification_status INTO STRICT manifest,source_state FROM history_archives WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.archive_id AND stored_count=object_count AND stored_bytes=total_bytes;
 IF raw->>'protocol' IS DISTINCT FROM 'zhiyu-passive-history-import-v1' OR raw->>'id' IS DISTINCT FROM NEW.id::text OR raw->>'archive_id' IS DISTINCT FROM NEW.archive_id::text OR raw->>'session_id' IS DISTINCT FROM NEW.session_id::text OR
 raw->'scope' IS DISTINCT FROM jsonb_build_object('environment_id',NEW.environment_id::text,'user_id',NEW.user_id::text) OR raw->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key OR raw->>'request_hash' IS DISTINCT FROM NEW.request_hash::text OR raw->>'mapping_hash' IS DISTINCT FROM NEW.mapping_hash::text OR raw->>'reviewed_hash' IS DISTINCT FROM NEW.reviewed_hash::text OR
 raw->'activated' IS DISTINCT FROM 'false'::jsonb OR raw->'bank_authority' IS DISTINCT FROM 'false'::jsonb OR raw->>'source_status' IS DISTINCT FROM NEW.source_status OR source_state<>NEW.source_status OR (raw->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at OR
 mapping->>'protocol' IS DISTINCT FROM 'zhiyu-passive-history-mapping-v1' OR mapping->>'archive_root_hash' IS DISTINCT FROM manifest->>'root_hash' OR mapping->>'source_environment_id' IS DISTINCT FROM manifest->'source'->>'environment_id' OR mapping->>'source_user_id' IS DISTINCT FROM manifest->'source'->>'user_id' OR mapping->>'source_namespace' IS DISTINCT FROM manifest->'source'->>'namespace' OR
 mapping->>'destination_environment_id' IS DISTINCT FROM NEW.environment_id::text OR mapping->>'destination_user_id' IS DISTINCT FROM NEW.user_id::text OR mapping->'activated' IS DISTINCT FROM 'false'::jsonb OR mapping->'bank_authority' IS DISTINCT FROM 'false'::jsonb OR jsonb_array_length(mapping->'entries') IS DISTINCT FROM jsonb_array_length(manifest->'objects') OR
 NEW.cursor<>0 OR NEW.generation<>0 OR NEW.attempts<>0 OR NEW.state NOT IN ('READY','COMPLETED') THEN RAISE EXCEPTION 'history import source or reviewed mapping differs' USING ERRCODE='23514';END IF;
 FOR i IN 0..jsonb_array_length(mapping->'entries')-1 LOOP
  entry:=mapping->'entries'->i;expected:=manifest->'objects'->i;
  IF entry->>'object_id' IS DISTINCT FROM expected->>'id' OR entry->>'source_raw_hash' IS DISTINCT FROM expected->>'raw_hash' OR length(entry->>'destination_id') NOT BETWEEN 1 AND 128 THEN RAISE EXCEPTION 'history mapping original differs' USING ERRCODE='23514';END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value->>'destination_id') FROM jsonb_array_elements(mapping->'entries'))<>jsonb_array_length(mapping->'entries') THEN RAISE EXCEPTION 'history mapping aliases duplicate' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION history_passive_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE mapping jsonb;entry jsonb;rawhash hash256;archive_ordinal integer;
BEGIN
 SELECT convert_from(mapping_original,'UTF8')::jsonb INTO STRICT mapping FROM history_imports WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND id=NEW.import_id AND archive_id=NEW.archive_id FOR SHARE;
 entry:=mapping->'entries'->(NEW.ordinal-1);
 SELECT original_hash,ordinal INTO STRICT rawhash,archive_ordinal FROM history_archive_objects WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND archive_id=NEW.archive_id AND id=NEW.object_id;
 IF entry->>'object_id' IS DISTINCT FROM NEW.object_id::text OR entry->>'source_raw_hash' IS DISTINCT FROM NEW.source_raw_hash::text OR entry->>'destination_id' IS DISTINCT FROM NEW.destination_id OR rawhash<>NEW.source_raw_hash OR archive_ordinal<>NEW.ordinal THEN RAISE EXCEPTION 'passive copy identity differs' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE FUNCTION history_import_closure() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE iid uuid;checkpoint integer;expected integer;actual integer;last_ordinal integer;current_state text;
BEGIN
 IF TG_TABLE_NAME='history_imports' THEN iid:=NEW.id;ELSE iid:=NEW.import_id;END IF;
 SELECT i.cursor,i.state,a.object_count INTO STRICT checkpoint,current_state,expected FROM history_imports i JOIN history_archives a ON(a.environment_id=i.environment_id AND a.user_id=i.user_id AND a.id=i.archive_id) WHERE i.environment_id=NEW.environment_id AND i.user_id=NEW.user_id AND i.id=iid;
 SELECT count(*),coalesce(max(ordinal),0) INTO actual,last_ordinal FROM history_passive_rows WHERE environment_id=NEW.environment_id AND user_id=NEW.user_id AND import_id=iid;
 IF checkpoint<>actual OR checkpoint<>last_ordinal OR checkpoint>expected OR (current_state='COMPLETED') IS DISTINCT FROM (checkpoint=expected) THEN RAISE EXCEPTION 'history import checkpoint differs from contiguous prefix' USING ERRCODE='23514';END IF;RETURN NULL;
END $$;
-- +goose StatementEnd
CREATE TRIGGER history_archive_original BEFORE INSERT ON history_archives FOR EACH ROW EXECUTE FUNCTION history_archive_guard();
CREATE TRIGGER history_archive_immutable BEFORE UPDATE OR DELETE ON history_archives FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER history_object_original BEFORE INSERT ON history_archive_objects FOR EACH ROW EXECUTE FUNCTION history_object_guard();
CREATE TRIGGER history_object_immutable BEFORE UPDATE OR DELETE ON history_archive_objects FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE CONSTRAINT TRIGGER history_archive_complete AFTER INSERT ON history_archives DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION history_archive_closure();
CREATE CONSTRAINT TRIGGER history_object_complete AFTER INSERT ON history_archive_objects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION history_archive_closure();
CREATE TRIGGER history_import_original BEFORE INSERT OR UPDATE ON history_imports FOR EACH ROW EXECUTE FUNCTION history_import_guard();
CREATE TRIGGER history_import_no_delete BEFORE DELETE ON history_imports FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE TRIGGER history_passive_original BEFORE INSERT ON history_passive_rows FOR EACH ROW EXECUTE FUNCTION history_passive_guard();
CREATE TRIGGER history_passive_immutable BEFORE UPDATE OR DELETE ON history_passive_rows FOR EACH ROW EXECUTE FUNCTION reject_immutable_row();
CREATE CONSTRAINT TRIGGER history_import_prefix AFTER INSERT OR UPDATE ON history_imports DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION history_import_closure();
CREATE CONSTRAINT TRIGGER history_passive_prefix AFTER INSERT ON history_passive_rows DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION history_import_closure();
REVOKE ALL ON history_archives,history_archive_objects,history_imports,history_passive_rows FROM zygo_app;
GRANT SELECT,INSERT ON history_archives,history_archive_objects,history_imports,history_passive_rows TO zygo_app;
GRANT UPDATE(state,paused,cursor,generation,attempts,updated_at) ON history_imports TO zygo_app;
-- +goose Down
-- +goose StatementBegin
DO $$ BEGIN RAISE EXCEPTION 'historical originals and passive checkpoints are forward only';END $$;
-- +goose StatementEnd
