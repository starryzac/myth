"""Prevent updates to confirmed version records; controlled demo deletes remain possible."""

import re
from collections.abc import Sequence

from alembic import op

revision: str = "0002_immutable_policy_versions"
down_revision: str | Sequence[str] | None = "0001_mvp_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION reject_policy_version_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'PolicyVersion is immutable; append a new version'
                USING ERRCODE = '23514';
        END $$
    """)
    op.execute("""
        CREATE TRIGGER policy_versions_immutable BEFORE UPDATE ON policy_versions
        FOR EACH ROW EXECUTE FUNCTION reject_policy_version_update()
    """)


def downgrade() -> None:
    if op.get_context().as_sql:
        raise ValueError("Offline downgrade is disabled; use a generated bf_test_ database")
    database = op.get_bind().engine.url.database
    if database is None or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None:
        raise ValueError("Downgrade requires a generated bf_test_<32 hex> database")
    op.execute("DROP TRIGGER policy_versions_immutable ON policy_versions")
    op.execute("DROP FUNCTION reject_policy_version_update()")
