"""Add an explicit global notification kind, without rewriting retained messages."""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_global_notifications"
down_revision: str | Sequence[str] | None = "0013_full_asset_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SOURCE_CHECK = (
    "(source_kind = 'QUESTION' AND session_id IS NOT NULL AND question_id IS NOT NULL "
    "AND question_revision IS NOT NULL AND question_revision >= 1) OR "
    "(source_kind = 'SINGLE_ACTION_BOUNDARY' AND session_id IS NULL "
    "AND question_id IS NULL AND question_revision IS NULL)"
)
NEW_SOURCE_CHECK = (
    "(source_kind = 'QUESTION' AND session_id IS NOT NULL AND question_id IS NOT NULL "
    "AND question_revision IS NOT NULL AND question_revision >= 1) OR "
    "(source_kind IN ('SINGLE_ACTION_BOUNDARY', 'GLOBAL_ACTION_SET_BOUNDARY') "
    "AND session_id IS NULL AND question_id IS NULL AND question_revision IS NULL)"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_intervention_outbox_source"), "intervention_outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_intervention_outbox_source"), "intervention_outbox", NEW_SOURCE_CHECK
    )


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM intervention_outbox WHERE source_kind='GLOBAL_ACTION_SET_BOUNDARY')
 THEN RAISE EXCEPTION 'Refusing to discard retained global notification history'; END IF;
END $$;
""")
    op.drop_constraint(op.f("ck_intervention_outbox_source"), "intervention_outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_intervention_outbox_source"), "intervention_outbox", OLD_SOURCE_CHECK
    )
