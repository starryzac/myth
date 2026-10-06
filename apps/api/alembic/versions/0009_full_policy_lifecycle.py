"""Independent FULL planning lifecycle; legacy financial rows are unchanged."""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_full_policy_lifecycle"

down_revision: str | Sequence[str] | None = "0008_command_delivery"

branch_labels: str | Sequence[str] | None = None

depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.execute(
        "\nCREATE TABLE full_policies (\n\tepoch_id UUID NOT NULL, \n\ttemplate_"
        "name VARCHAR(64) NOT NULL, \n\tdsl_version VARCHAR(16) DEFAULT 'FULL"
        "_V1' NOT NULL, \n\tname VARCHAR(120) NOT NULL, \n\tstatus VARCHAR(24) "
        "DEFAULT 'CONFIRMED' NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZON"
        "E NOT NULL, \n\tuser_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tcreated"
        "_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT "
        "pk_full_policies PRIMARY KEY (id), \n\tCONSTRAINT fk_full_policies_e"
        "poch_id_audit_epochs FOREIGN KEY(epoch_id, user_id) REFERENCES aud"
        "it_epochs (id, user_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_full_p"
        "olicy_epoch_identity UNIQUE (id, epoch_id, user_id), \n\tCONSTRAINT "
        "ck_full_policies_template CHECK (dsl_version = 'FULL_V1' AND templ"
        "ate_name IN ('DatedExpensePolicy', 'PeriodicTransferPolicy', 'Asse"
        "tAuthorizationPolicy', 'RecoveryPolicy', 'GoalAllocationPolicy', '"
        "CrossGoalReallocationPolicy', 'SeasonalReservePolicy', 'Interventi"
        "onPolicy')), \n\tCONSTRAINT ck_full_policies_status CHECK (status IN"
        " ('ACTIVE', 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED')), \n\tCO"
        "NSTRAINT ck_full_policies_time CHECK (length(name) BETWEEN 1 AND 1"
        "20 AND updated_at >= created_at), \n\tCONSTRAINT uq_full_policies_id"
        " UNIQUE (id, user_id), \n\tCONSTRAINT fk_full_policies_user_id_users"
        " FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT\n)\n\n"
    )

    op.execute("CREATE INDEX ix_full_policies_user_id ON full_policies (user_id)")

    op.execute(
        "CREATE INDEX ix_full_policy_epoch_status ON full_policies (user_id, epoch_id, status, id)"
    )

    op.execute(
        "\nCREATE TABLE full_policy_versions (\n\tpolicy_id UUID NOT NULL, \n\tv"
        "ersion_number INTEGER NOT NULL, \n\tconfiguration JSONB NOT NULL, \n\t"
        "content_hash VARCHAR(64) NOT NULL, \n\tprevious_hash VARCHAR(64), \n\t"
        "summary TEXT NOT NULL, \n\tconfirmation JSONB NOT NULL, \n\tconfirmed_"
        "at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tvalid_from TIMESTAMP WITH "
        "TIME ZONE NOT NULL, \n\tvalid_until TIMESTAMP WITH TIME ZONE, \n\tchan"
        "ge_reason TEXT NOT NULL, \n\tevidence_ids JSONB NOT NULL, \n\timpact_a"
        "nalysis JSONB NOT NULL, \n\tuser_id UUID NOT NULL, \n\tid UUID NOT NUL"
        "L, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tCONSTRAINT pk_full_policy_versions PRIMARY KEY (id), \n\tCONSTRAINT"
        " fk_full_policy_versions_policy_id_full_policies FOREIGN KEY(polic"
        "y_id, user_id) REFERENCES full_policies (id, user_id) ON DELETE RE"
        "STRICT, \n\tCONSTRAINT uq_full_policy_version_number UNIQUE (policy_"
        "id, version_number), \n\tCONSTRAINT uq_full_policy_version_identity "
        "UNIQUE (id, policy_id, user_id), \n\tCONSTRAINT ck_full_policy_versi"
        "ons_number CHECK (version_number > 0), \n\tCONSTRAINT ck_full_policy"
        "_versions_hashes CHECK (content_hash ~ '^[0-9a-f]{64}$' AND (previ"
        "ous_hash IS NULL OR previous_hash ~ '^[0-9a-f]{64}$')), \n\tCONSTRAI"
        "NT ck_full_policy_versions_json CHECK (jsonb_typeof(configuration)"
        " = 'object' AND octet_length(configuration::text) <= 1048576 AND j"
        "sonb_typeof(confirmation) = 'object' AND jsonb_typeof(evidence_ids"
        ") = 'array' AND jsonb_typeof(impact_analysis) = 'object'), \n\tCONST"
        "RAINT ck_full_policy_versions_time CHECK (confirmed_at >= created_"
        "at AND (valid_until IS NULL OR valid_until > valid_from)), \n\tCONST"
        "RAINT uq_full_policy_versions_id UNIQUE (id, user_id), \n\tCONSTRAIN"
        "T fk_full_policy_versions_user_id_users FOREIGN KEY(user_id) REFER"
        "ENCES users (id) ON DELETE RESTRICT\n)\n\n"
    )

    op.execute("CREATE INDEX ix_full_policy_versions_user_id ON full_policy_versions (user_id)")

    op.execute(
        "\nCREATE TABLE full_policy_commands (\n\tepoch_id UUID NOT NULL, \n\tpo"
        "licy_id UUID NOT NULL, \n\tversion_id UUID NOT NULL, \n\tcommand_numbe"
        "r INTEGER NOT NULL, \n\tprevious_hash VARCHAR(64), \n\tkind VARCHAR(24"
        ") NOT NULL, \n\tidempotency_key VARCHAR(160) NOT NULL, \n\trequest_has"
        "h VARCHAR(64) NOT NULL, \n\trequest JSONB NOT NULL, \n\tprevious_statu"
        "s VARCHAR(24), \n\tresulting_status VARCHAR(24) NOT NULL, \n\tresult J"
        "SONB NOT NULL, \n\tresult_hash VARCHAR(64) NOT NULL, \n\tuser_id UUID "
        "NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZON"
        "E DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_full_policy_commands PRI"
        "MARY KEY (id), \n\tCONSTRAINT fk_full_policy_commands_epoch_id_audit"
        "_epochs FOREIGN KEY(epoch_id, user_id) REFERENCES audit_epochs (id"
        ", user_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_full_policy_command"
        "s_policy_id_full_policies FOREIGN KEY(policy_id, epoch_id, user_id"
        ") REFERENCES full_policies (id, epoch_id, user_id) ON DELETE RESTR"
        "ICT, \n\tCONSTRAINT fk_full_policy_commands_version_id_full_policy_v"
        "ersions FOREIGN KEY(version_id, policy_id, user_id) REFERENCES ful"
        "l_policy_versions (id, policy_id, user_id) ON DELETE RESTRICT, \n\tC"
        "ONSTRAINT uq_full_policy_command_key UNIQUE (user_id, idempotency_"
        "key), \n\tCONSTRAINT uq_full_policy_command_number UNIQUE (policy_id"
        ", command_number), \n\tCONSTRAINT ck_full_policy_commands_number CHE"
        "CK (command_number > 0), \n\tCONSTRAINT ck_full_policy_commands_hash"
        "es CHECK (request_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9"
        "a-f]{64}$' AND (previous_hash IS NULL OR previous_hash ~ '^[0-9a-f"
        "]{64}$')), \n\tCONSTRAINT ck_full_policy_commands_kind CHECK (kind I"
        "N ('CREATE', 'CHANGE', 'SUSPEND', 'REVOKE', 'RESUME', 'REFRESH_TIM"
        "E') AND length(idempotency_key) BETWEEN 1 AND 160), \n\tCONSTRAINT c"
        "k_full_policy_commands_status CHECK (resulting_status IN ('ACTIVE'"
        ", 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED') AND (previous_st"
        "atus IS NULL OR previous_status IN ('ACTIVE', 'CONFIRMED', 'SUSPEN"
        "DED', 'EXPIRED', 'REVOKED'))), \n\tCONSTRAINT ck_full_policy_command"
        "s_json CHECK (jsonb_typeof(request) = 'object' AND octet_length(re"
        "quest::text) <= 1048576 AND jsonb_typeof(result) = 'object' AND oc"
        "tet_length(result::text) <= 1048576), \n\tCONSTRAINT uq_full_policy_"
        "commands_id UNIQUE (id, user_id), \n\tCONSTRAINT fk_full_policy_comm"
        "ands_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON D"
        "ELETE RESTRICT\n)\n\n"
    )

    op.execute("CREATE INDEX ix_full_policy_commands_user_id ON full_policy_commands (user_id)")

    op.execute(
        "CREATE FUNCTION guard_full_policy_identity() RETURNS trigger LANGU"
        "AGE plpgsql AS $$\nBEGIN\n IF (NEW.id, NEW.user_id, NEW.created_at, "
        "NEW.epoch_id, NEW.template_name, NEW.dsl_version)\n IS DISTINCT FRO"
        "M\n (OLD.id, OLD.user_id, OLD.created_at, OLD.epoch_id, OLD.templat"
        "e_name, OLD.dsl_version) THEN\n  RAISE EXCEPTION 'FULL planning pol"
        "icy identity is immutable';\n END IF;\n RETURN NEW;\nEND $$"
    )

    op.execute(
        "CREATE FUNCTION guard_full_policy_history() RETURNS trigger LANGUA"
        "GE plpgsql AS $$\nBEGIN\n RAISE EXCEPTION 'FULL planning history is "
        "immutable';\n RETURN NULL;\nEND $$"
    )

    op.execute(
        "CREATE TRIGGER full_policy_identity BEFORE UPDATE ON full_policies"
        "\nFOR EACH ROW EXECUTE FUNCTION guard_full_policy_identity()"
    )

    op.execute(
        "CREATE TRIGGER full_policy_retained BEFORE DELETE ON full_policies"
        "\nFOR EACH ROW EXECUTE FUNCTION guard_full_policy_history()"
    )

    op.execute(
        "CREATE TRIGGER full_policy_versions_immutable BEFORE UPDATE OR DEL"
        "ETE ON full_policy_versions\nFOR EACH ROW EXECUTE FUNCTION guard_fu"
        "ll_policy_history()"
    )

    op.execute(
        "CREATE TRIGGER full_policy_commands_immutable BEFORE UPDATE OR DEL"
        "ETE ON full_policy_commands\nFOR EACH ROW EXECUTE FUNCTION guard_fu"
        "ll_policy_history()"
    )


def downgrade() -> None:

    op.execute(
        "DO $$ BEGIN\n IF EXISTS (SELECT 1 FROM full_policies) OR EXISTS (SE"
        "LECT 1 FROM full_policy_versions)\n OR EXISTS (SELECT 1 FROM full_p"
        "olicy_commands) THEN\n RAISE EXCEPTION 'Refusing to discard FULL pl"
        "anning history';\n END IF;\nEND $$"
    )

    op.drop_table("full_policy_commands")

    op.drop_table("full_policy_versions")

    op.drop_table("full_policies")

    op.execute("DROP FUNCTION guard_full_policy_history()")

    op.execute("DROP FUNCTION guard_full_policy_identity()")
