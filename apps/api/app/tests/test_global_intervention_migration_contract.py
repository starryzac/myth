"""Pure DDL intent only; the actual PostgreSQL preservation node remains separate."""

import runpy
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

from app.db.full_models import InterventionOutbox
from sqlalchemy import CheckConstraint, Table


def test_additive_check_migration_has_no_row_write_and_refuses_lossy_downgrade() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0014_global_action_set_notifications.py"
    )
    module = runpy.run_path(str(path))
    upgrade, downgrade = module["upgrade"], module["downgrade"]
    operations = MagicMock()
    operations.f.side_effect = lambda value: value
    upgrade.__globals__["op"] = operations
    upgrade()
    operations.execute.assert_not_called()
    operations.drop_constraint.assert_called_once_with(
        "ck_intervention_outbox_source", "intervention_outbox", type_="check"
    )
    operations.create_check_constraint.assert_called_once_with(
        "ck_intervention_outbox_source", "intervention_outbox", module["NEW_SOURCE_CHECK"]
    )
    constraint = next(
        row
        for row in cast(Table, InterventionOutbox.__table__).constraints
        if isinstance(row, CheckConstraint) and row.name == "ck_intervention_outbox_source"
    )
    assert str(constraint.sqltext) == module["NEW_SOURCE_CHECK"]
    operations.reset_mock()
    downgrade.__globals__["op"] = operations
    downgrade()
    calls: list[Any] = operations.method_calls
    assert calls[0][0] == "execute"
    sql = calls[0][1][0]
    assert "SELECT 1 FROM intervention_outbox WHERE source_kind='GLOBAL_ACTION_SET_BOUNDARY'" in sql
    assert "RAISE EXCEPTION 'Refusing to discard retained global notification history'" in sql
    assert all(token not in sql.upper() for token in ("UPDATE ", "DELETE ", "TRUNCATE "))
    assert operations.create_check_constraint.call_args.args[-1] == module["OLD_SOURCE_CHECK"]
    assert module["revision"] == "0014_global_notifications" and len(module["revision"]) <= 32
