"Durable action delivery; additive metadata, no legacy financial rewrite."

from collections.abc import Sequence

from alembic import op

revision: str = "0008_command_delivery"

down_revision: str | Sequence[str] | None = "0007_external_bank_facts"

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.execute(
        "\nCREATE TABLE command_outbox (\n\taction_plan_id UUID NOT NULL, \n\te"
        "poch_id UUID NOT NULL, \n\tprotocol_version VARCHAR(40) DEFAULT 'ac"
        "tion-delivery-v1' NOT NULL, \n\troot_id UUID NOT NULL, \n\trequest_ha"
        "sh VARCHAR(64) NOT NULL, \n\teffect_hash VARCHAR(64) NOT NULL, \n\tba"
        "nk_idempotency_key VARCHAR(160) NOT NULL, \n\tpayload JSONB NOT NUL"
        "L, \n\tpayload_hash VARCHAR(64) NOT NULL, \n\tstate VARCHAR(24) DEFAU"
        "LT 'PENDING' NOT NULL, \n\tattempt_count INTEGER DEFAULT '0' NOT NU"
        "LL, \n\tpublished_at TIMESTAMP WITH TIME ZONE, \n\tacknowledged_at TI"
        "MESTAMP WITH TIME ZONE, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT"
        " NULL, \n\tlast_error TEXT, \n\tuser_id UUID NOT NULL, \n\tid UUID NOT "
        "NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NUL"
        "L, \n\tCONSTRAINT pk_command_outbox PRIMARY KEY (id), \n\tCONSTRAINT "
        "fk_command_outbox_epoch_id_audit_epochs FOREIGN KEY(epoch_id, use"
        "r_id) REFERENCES audit_epochs (id, user_id) ON DELETE RESTRICT, \n"
        "\tCONSTRAINT uq_command_outbox_action UNIQUE (action_plan_id), \n\tC"
        "ONSTRAINT ck_command_outbox_identity CHECK (protocol_version = 'a"
        "ction-delivery-v1' AND root_id = action_plan_id), \n\tCONSTRAINT ck"
        "_command_outbox_hashes CHECK (request_hash ~ '^[0-9a-f]{64}$' AND"
        " effect_hash ~ '^[0-9a-f]{64}$' AND payload_hash ~ '^[0-9a-f]{64}"
        "$'), \n\tCONSTRAINT ck_command_outbox_payload CHECK (jsonb_typeof(p"
        "ayload) = 'object' AND octet_length(payload::text) <= 1048576), \n"
        "\tCONSTRAINT ck_command_outbox_attempts CHECK (length(bank_idempot"
        "ency_key) BETWEEN 1 AND 160 AND attempt_count >= 0), \n\tCONSTRAINT"
        " ck_command_outbox_state CHECK (state IN ('PENDING', 'DELIVERED',"
        " 'STOPPED')), \n\tCONSTRAINT ck_command_outbox_time CHECK (updated_"
        "at >= created_at AND (published_at IS NULL OR published_at >= cre"
        "ated_at) AND (acknowledged_at IS NULL OR acknowledged_at >= creat"
        "ed_at)), \n\tCONSTRAINT uq_command_outbox_id UNIQUE (id, user_id), "
        "\n\tCONSTRAINT fk_command_outbox_user_id_users FOREIGN KEY(user_id)"
        " REFERENCES users (id) ON DELETE RESTRICT\n)\n\n"
    )

    op.execute(
        "CREATE INDEX ix_command_outbox_pending ON command_outbox (user_id, state, created_at, id)"
    )

    op.execute("CREATE INDEX ix_command_outbox_user_id ON command_outbox (user_id)")

    op.execute(
        "\nCREATE TABLE command_inbox (\n\toutbox_id UUID NOT NULL, \n\tconsume"
        "r_ref VARCHAR(80) NOT NULL, \n\tpayload_hash VARCHAR(64) NOT NULL, "
        "\n\tattempt_id UUID NOT NULL, \n\tstate VARCHAR(32) DEFAULT 'RECEIVED"
        "' NOT NULL, \n\tattempt_count INTEGER DEFAULT '0' NOT NULL, \n\trecei"
        "ved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstarted_at TIMESTAMP "
        "WITH TIME ZONE, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tupdated"
        "_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tsource_action_status VAR"
        "CHAR(24), \n\tresult JSONB, \n\tlast_error TEXT, \n\tuser_id UUID NOT N"
        "ULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DE"
        "FAULT now() NOT NULL, \n\tCONSTRAINT pk_command_inbox PRIMARY KEY ("
        "id), \n\tCONSTRAINT fk_command_inbox_outbox_id_command_outbox FOREI"
        "GN KEY(outbox_id, user_id) REFERENCES command_outbox (id, user_id"
        ") ON DELETE RESTRICT, \n\tCONSTRAINT uq_command_inbox_message_consu"
        "mer UNIQUE (outbox_id, consumer_ref), \n\tCONSTRAINT ck_command_inb"
        "ox_identity CHECK (length(consumer_ref) BETWEEN 1 AND 80 AND payl"
        "oad_hash ~ '^[0-9a-f]{64}$' AND attempt_count >= 0), \n\tCONSTRAINT"
        " ck_command_inbox_state CHECK (state IN ('RECEIVED', 'PROCESSING'"
        ", 'WAITING_CONFIRMATION', 'UNRESOLVED', 'SERVICE_RECEIPT_VERIFIED"
        "', 'STOPPED', 'FAILED')), \n\tCONSTRAINT ck_command_inbox_result CH"
        "ECK (result IS NULL OR jsonb_typeof(result) = 'object'), \n\tCONSTR"
        "AINT ck_command_inbox_time CHECK (received_at >= created_at AND u"
        "pdated_at >= received_at AND (started_at IS NULL OR started_at >="
        " received_at) AND (finished_at IS NULL OR finished_at >= received"
        "_at)), \n\tCONSTRAINT uq_command_inbox_id UNIQUE (id, user_id), \n\tC"
        "ONSTRAINT fk_command_inbox_user_id_users FOREIGN KEY(user_id) REF"
        "ERENCES users (id) ON DELETE RESTRICT\n)\n\n"
    )

    op.execute("CREATE INDEX ix_command_inbox_user_id ON command_inbox (user_id)")

    op.execute(
        "\nCREATE TABLE command_delivery_attempts (\n\toutbox_id UUID NOT NUL"
        "L, \n\tinbox_id UUID NOT NULL, \n\tattempt_number INTEGER NOT NULL, \n"
        "\tstate VARCHAR(32) DEFAULT 'RECEIVED' NOT NULL, \n\tstarted_at TIME"
        "STAMP WITH TIME ZONE NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME "
        "ZONE, \n\tsource_action_status VARCHAR(24), \n\tresult JSONB, \n\terror"
        " TEXT, \n\tuser_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at "
        "TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_"
        "command_delivery_attempts PRIMARY KEY (id), \n\tCONSTRAINT fk_comma"
        "nd_delivery_attempts_outbox_id_command_outbox FOREIGN KEY(outbox_"
        "id, user_id) REFERENCES command_outbox (id, user_id) ON DELETE RE"
        "STRICT, \n\tCONSTRAINT fk_command_delivery_attempts_inbox_id_comman"
        "d_inbox FOREIGN KEY(inbox_id, user_id) REFERENCES command_inbox ("
        "id, user_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_command_delivery"
        "_attempt_number UNIQUE (inbox_id, attempt_number), \n\tCONSTRAINT c"
        "k_command_delivery_attempts_number CHECK (attempt_number > 0), \n\t"
        "CONSTRAINT ck_command_delivery_attempts_state CHECK (state IN ('R"
        "ECEIVED', 'PROCESSING', 'WAITING_CONFIRMATION', 'UNRESOLVED', 'SE"
        "RVICE_RECEIPT_VERIFIED', 'STOPPED', 'FAILED')), \n\tCONSTRAINT ck_c"
        "ommand_delivery_attempts_time CHECK (started_at >= created_at AND"
        " (finished_at IS NULL OR finished_at >= started_at)), \n\tCONSTRAIN"
        "T ck_command_delivery_attempts_result CHECK (result IS NULL OR js"
        "onb_typeof(result) = 'object'), \n\tCONSTRAINT uq_command_delivery_"
        "attempts_id UNIQUE (id, user_id), \n\tCONSTRAINT fk_command_deliver"
        "y_attempts_user_id_users FOREIGN KEY(user_id) REFERENCES users (i"
        "d) ON DELETE RESTRICT\n)\n\n"
    )

    op.execute(
        "CREATE INDEX ix_command_delivery_attempts_user_id ON command_delivery_attempts (user_id)"
    )

    op.execute(
        "CREATE FUNCTION guard_command_outbox_identity() RETURNS trigger L"
        "ANGUAGE plpgsql AS $$\nBEGIN\n IF (NEW.id, NEW.user_id, NEW.created"
        "_at, NEW.action_plan_id, NEW.epoch_id,\n     NEW.protocol_version,"
        " NEW.root_id, NEW.request_hash, NEW.effect_hash,\n     NEW.bank_id"
        "empotency_key, NEW.payload, NEW.payload_hash)\n IS DISTINCT FROM ("
        "OLD.id, OLD.user_id, OLD.created_at, OLD.action_plan_id, OLD.epoc"
        "h_id,\n     OLD.protocol_version, OLD.root_id, OLD.request_hash, O"
        "LD.effect_hash,\n     OLD.bank_idempotency_key, OLD.payload, OLD.p"
        "ayload_hash) THEN\n  RAISE EXCEPTION 'durable delivery identity is"
        " immutable';\n END IF;\n RETURN NEW;\nEND $$"
    )

    op.execute(
        "CREATE TRIGGER command_outbox_identity BEFORE UPDATE ON command_o"
        "utbox FOR EACH ROW EXECUTE FUNCTION guard_command_outbox_identity"
        "()"
    )


def downgrade() -> None:

    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM command_outbox) OR EXISTS (S"
        "ELECT 1 FROM command_inbox) OR EXISTS (SELECT 1 FROM command_deli"
        "very_attempts) THEN RAISE EXCEPTION 'Refusing to discard durable "
        "delivery history'; END IF; END $$"
    )

    op.drop_table("command_delivery_attempts")

    op.drop_table("command_inbox")

    op.drop_table("command_outbox")

    op.execute("DROP FUNCTION guard_command_outbox_identity()")
