"""Alembic environment.

URL resolution order: an open connection passed by the app (config.attributes["connection"]),
`alembic -x url=...`, then the application's own settings (DATABASE_URL / .env / Settings page).
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.database.migrate import VERSION_TABLE
from app.models.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    cli = context.get_x_argument(as_dictionary=True).get("url")
    if cli:
        return cli
    from app.config.settings import get_settings

    return get_settings().database_url


def _configure(connection=None, url=None):
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        literal_binds=connection is None,
        compare_type=True,
        version_table=VERSION_TABLE,
        render_as_batch=True,  # SQLite cannot ALTER in place; batch mode copies the table instead
    )


def run_migrations_offline() -> None:
    _configure(url=_url())
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _configure(connection=supplied)
        with context.begin_transaction():
            context.run_migrations()
        return
    url = _url()
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    with engine.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
