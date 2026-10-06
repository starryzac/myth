"""Optional tenant-bound links preserve original decisions and subsequent revalidation.

Downgrade is restricted to expendable generated test databases: it drops the nullable
relation projections. Frozen input JSON remains, but a subsequent upgrade does not
reconstruct those links or claim to backfill complete historical records.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_decision_trace"
down_revision: str | Sequence[str] | None = "0004_execution_bank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("decision_runs", sa.Column("parent_run_id", sa.UUID(), nullable=True))
    op.add_column("decision_runs", sa.Column("subject_action_plan_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_decision_runs_parent_run_id_decision_runs",
        "decision_runs",
        "decision_runs",
        ["parent_run_id", "user_id"],
        ["id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_decision_runs_subject_action_plan_id_action_plans",
        "decision_runs",
        "action_plans",
        ["subject_action_plan_id", "user_id"],
        ["id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_decision_runs_user_subject",
        "decision_runs",
        ["user_id", "subject_action_plan_id", "as_of", "id"],
    )
    op.create_index(
        "ix_decision_runs_user_parent",
        "decision_runs",
        ["user_id", "parent_run_id", "as_of", "id"],
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        raise ValueError("Offline downgrade is disabled; use a generated bf_test_ database")
    database = op.get_bind().engine.url.database
    if database is None or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None:
        raise ValueError("Downgrade requires a generated bf_test_<32 hex> database")
    op.drop_index("ix_decision_runs_user_parent", table_name="decision_runs")
    op.drop_index("ix_decision_runs_user_subject", table_name="decision_runs")
    op.drop_constraint(
        "fk_decision_runs_subject_action_plan_id_action_plans", "decision_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_decision_runs_parent_run_id_decision_runs", "decision_runs", type_="foreignkey"
    )
    op.drop_column("decision_runs", "subject_action_plan_id")
    op.drop_column("decision_runs", "parent_run_id")
