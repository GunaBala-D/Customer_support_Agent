"""Run Alembic migrations from application code (startup, the Settings page, tests, CI).

Safety rules, because the target may be a database someone else also uses:
  * own version table (`support_alembic_version`), so another app's `alembic_version` is never touched
  * empty database                   -> run all migrations
  * all baseline tables, no version  -> "legacy": created by the old create_all(); stamp 0001, then upgrade
  * only some of our tables          -> refuse (never modify a schema we don't recognise)
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

BACKEND_DIR = Path(__file__).resolve().parents[2]
VERSION_TABLE = "support_alembic_version"
BASELINE_REVISION = "0001"
BASELINE_TABLES = frozenset({
    "conversations", "customers", "products", "orders", "order_items", "support_tickets", "messages",
    "tool_calls", "documents", "document_chunks", "ticket_notes", "events",
})


class MigrationError(Exception):
    pass


def _config(connection=None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def database_state(engine: Engine) -> dict:
    """Classify a database without changing it. state: empty | legacy | behind | current | conflict | unknown_revision"""
    tables = set(inspect(engine).get_table_names())
    ours = tables & BASELINE_TABLES
    head = head_revision()
    if VERSION_TABLE in tables:
        with engine.connect() as c:
            rows = c.execute(text(f"select version_num from {VERSION_TABLE}")).fetchall()
        rev = rows[0][0] if rows else None
        known = {r.revision for r in ScriptDirectory.from_config(_config()).walk_revisions()}
        state = "current" if rev == head else "behind" if rev in known else "unknown_revision"
        return {"state": state, "revision": rev, "head": head, "other_tables": len(tables - ours - {VERSION_TABLE})}
    if not ours:
        return {"state": "empty", "revision": None, "head": head, "other_tables": len(tables)}
    if ours == BASELINE_TABLES:
        return {"state": "legacy", "revision": None, "head": head, "other_tables": len(tables - ours)}
    return {"state": "conflict", "revision": None, "head": head, "other_tables": len(tables - ours),
            "detail": f"has only some application tables: {sorted(ours)}"}


def upgrade_to_head(engine: Engine) -> dict:
    status = database_state(engine)
    if status["state"] == "conflict":
        raise MigrationError(f"Refusing to migrate: database {status['detail']}")
    if status["state"] == "unknown_revision":
        raise MigrationError(f"Database is at revision {status['revision']!r}, which this version of the app does not know")
    with engine.begin() as connection:
        cfg = _config(connection)
        if status["state"] == "legacy":
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
    return {**status, "state": "current", "revision": head_revision(), "adopted_legacy": status["state"] == "legacy"}


def downgrade(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        command.downgrade(_config(connection), revision)
