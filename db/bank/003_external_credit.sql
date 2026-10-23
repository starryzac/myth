-- Frozen additive external credit protocol; installed only by explicit MigrateExternalCredit.
CREATE SEQUENCE bank.external_credit_sequence;
CREATE TABLE bank.external_credits (
 sequence bigint NOT NULL DEFAULT nextval('bank.external_credit_sequence') UNIQUE,
 namespace text NOT NULL,user_id text NOT NULL,authority_id text NOT NULL,event_id text NOT NULL,
 account_id text NOT NULL,origin_id text NOT NULL,evidence_id text NOT NULL,
 amount_cents bigint NOT NULL CHECK(amount_cents BETWEEN 1 AND 9000000000000000),
 occurred_at timestamptz NOT NULL,credited_at timestamptz NOT NULL CHECK(credited_at>=occurred_at),
 balance_before_cents bigint NOT NULL CHECK(balance_before_cents BETWEEN 0 AND 9000000000000000),
 balance_after_cents bigint NOT NULL CHECK(balance_after_cents BETWEEN 1 AND 9000000000000000),
 event_original bytea NOT NULL CHECK(octet_length(event_original) BETWEEN 1 AND 16384),
 event_hash text NOT NULL CHECK(event_hash=encode(sha256(event_original),'hex')),
 authority_signature_hash text NOT NULL CHECK(authority_signature_hash ~ '^[0-9a-f]{64}$'),
 record_original bytea NOT NULL CHECK(octet_length(record_original) BETWEEN 1 AND 65536),
 record_hash text NOT NULL CHECK(record_hash=encode(sha256(record_original),'hex')),
 credited_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(namespace,authority_id,event_id),UNIQUE(namespace,user_id,origin_id),UNIQUE(namespace,user_id,evidence_id),
 FOREIGN KEY(namespace,user_id,origin_id) REFERENCES bank.origins(namespace,user_id,origin_id),
 FOREIGN KEY(namespace,user_id,account_id) REFERENCES bank.accounts(namespace,user_id,account_id),
 CHECK(balance_after_cents::numeric=balance_before_cents::numeric+amount_cents::numeric)
);
CREATE INDEX external_credit_inventory ON bank.external_credits(namespace,user_id,sequence);
CREATE TABLE bank.external_credit_postings (
 id text PRIMARY KEY CHECK(id ~ '^[0-9a-f]{64}$'),namespace text NOT NULL,authority_id text NOT NULL,event_id text NOT NULL,
 leg_number integer NOT NULL CHECK(leg_number BETWEEN 1 AND 4),
 dimension text NOT NULL CHECK(dimension IN ('ECONOMIC','INCOME_QUALIFICATION')),
 ledger_id text NOT NULL,direction text NOT NULL CHECK(direction IN ('DEBIT','CREDIT')),
 amount_cents bigint NOT NULL CHECK(amount_cents BETWEEN 1 AND 9000000000000000),occurred_at timestamptz NOT NULL,
 UNIQUE(namespace,authority_id,event_id,leg_number),
 FOREIGN KEY(namespace,authority_id,event_id) REFERENCES bank.external_credits(namespace,authority_id,event_id)
);
CREATE FUNCTION bank.reject_credit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'external credit originals are immutable';END $$;
CREATE TRIGGER external_credit_original BEFORE UPDATE OR DELETE ON bank.external_credits FOR EACH ROW EXECUTE FUNCTION bank.reject_credit_mutation();
CREATE TRIGGER external_credit_posting_original BEFORE UPDATE OR DELETE ON bank.external_credit_postings FOR EACH ROW EXECUTE FUNCTION bank.reject_credit_mutation();
CREATE FUNCTION bank.check_credit_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE credit bank.external_credits%ROWTYPE; leg record;
BEGIN
 SELECT * INTO credit FROM bank.external_credits WHERE namespace=NEW.namespace AND authority_id=NEW.authority_id AND event_id=NEW.event_id;
 IF credit.credited_xid<>pg_current_xact_id() THEN RAISE EXCEPTION 'external credit posting must be in original credit transaction';END IF;
 IF (SELECT count(*) FROM bank.external_credit_postings WHERE namespace=NEW.namespace AND authority_id=NEW.authority_id AND event_id=NEW.event_id)<>4 THEN RAISE EXCEPTION 'external credit requires four original postings';END IF;
 FOR leg IN SELECT * FROM bank.external_credit_postings WHERE namespace=NEW.namespace AND authority_id=NEW.authority_id AND event_id=NEW.event_id LOOP
  IF leg.amount_cents<>credit.amount_cents OR leg.occurred_at<>credit.occurred_at OR
     leg.dimension<>(CASE WHEN leg.leg_number<=2 THEN 'ECONOMIC' ELSE 'INCOME_QUALIFICATION' END) OR
     leg.direction<>(CASE WHEN leg.leg_number IN (1,3) THEN 'DEBIT' ELSE 'CREDIT' END) OR
     leg.ledger_id<>(CASE leg.leg_number WHEN 1 THEN 'external:'||credit.authority_id WHEN 2 THEN 'cash:'||credit.account_id WHEN 3 THEN 'external-income:'||credit.authority_id ELSE 'income:'||credit.origin_id||':'||credit.account_id END)
  THEN RAISE EXCEPTION 'external credit posting differs from original fact';END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM bank.origins WHERE namespace=credit.namespace AND user_id=credit.user_id AND origin_id=credit.origin_id AND original_account_id=credit.account_id AND original_cents=credit.amount_cents AND occurred_at=credit.occurred_at AND evidence_hash=credit.record_hash) THEN RAISE EXCEPTION 'external credit income anchor differs from original';END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER external_credit_complete AFTER INSERT ON bank.external_credits DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bank.check_credit_complete();
CREATE CONSTRAINT TRIGGER external_credit_posting_complete AFTER INSERT ON bank.external_credit_postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bank.check_credit_complete();
