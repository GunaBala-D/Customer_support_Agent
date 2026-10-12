import subprocess
import sys

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.database.migrate import (
    BACKEND_DIR,
    BASELINE_TABLES,
    VERSION_TABLE,
    MigrationError,
    database_state,
    downgrade,
    head_revision,
    upgrade_to_head,
)
from app.models.db import Base


@pytest.fixture
def engine(tmp_path):
    e = create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    yield e
    e.dispose()


def tables(engine):
    return set(inspect(engine).get_table_names())


def test_fresh_database_gets_every_table_at_head(engine):
    assert database_state(engine)["state"] == "empty"
    result = upgrade_to_head(engine)
    assert result["state"] == "current" and result["revision"] == head_revision() == "0002"
    assert tables(engine) == BASELINE_TABLES | {"users", VERSION_TABLE}


def test_migrations_match_the_models_exactly(engine):
    """Fails when someone edits models/db.py without writing a migration."""
    upgrade_to_head(engine)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "version_table": VERSION_TABLE})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], f"models and migrations have drifted: {diff}"


def test_upgrade_is_idempotent(engine):
    upgrade_to_head(engine)
    again = upgrade_to_head(engine)
    assert again["state"] == "current" and database_state(engine)["state"] == "current"


def test_legacy_database_from_create_all_is_adopted_and_keeps_its_data(engine):
    """Databases made before Alembic existed (create_all) must be stamped, not rebuilt."""
    legacy = [t for t in Base.metadata.sorted_tables if t.name != "users"]
    Base.metadata.create_all(engine, tables=legacy)
    with engine.begin() as c:
        c.execute(text("insert into customers (id, name, email, tier, created_at) values ('CUST-9001','Legacy Lee','l@x.io','standard','2026-01-01')"))
    assert database_state(engine)["state"] == "legacy"
    result = upgrade_to_head(engine)
    assert result["adopted_legacy"] is True and "users" in tables(engine)
    with engine.connect() as c:
        assert c.execute(text("select name from customers where id='CUST-9001'")).scalar() == "Legacy Lee"


def test_partial_schema_is_refused_and_left_untouched(engine):
    with engine.begin() as c:
        c.execute(text("create table customers (id integer primary key, secret text)"))
    assert database_state(engine)["state"] == "conflict"
    with pytest.raises(MigrationError):
        upgrade_to_head(engine)
    assert tables(engine) == {"customers"}  # nothing was created or altered


def test_other_applications_tables_are_left_alone(engine):
    with engine.begin() as c:
        c.execute(text("create table other_app_things (id integer primary key)"))
        c.execute(text("create table alembic_version (version_num varchar(32))"))  # someone else's version table
        c.execute(text("insert into alembic_version values ('abc')"))
    upgrade_to_head(engine)
    assert {"other_app_things", "alembic_version", "users"} <= tables(engine)
    with engine.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar() == "abc"


def test_downgrade_and_upgrade_round_trip(engine):
    upgrade_to_head(engine)
    downgrade(engine, "0001")
    assert "users" not in tables(engine) and database_state(engine)["state"] == "behind"
    assert upgrade_to_head(engine)["revision"] == "0002" and "users" in tables(engine)


def test_documented_cli_command_works(tmp_path):
    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    out = subprocess.run([sys.executable, "-m", "alembic", "-x", f"url={url}", "upgrade", "head"],
                         cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    e = create_engine(url)
    assert "users" in tables(e)
    e.dispose()
