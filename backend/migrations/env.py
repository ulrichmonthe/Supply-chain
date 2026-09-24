"""Alembic environment.

Two things are deliberate here.

The URL comes from the application's own settings, never from alembic.ini, so the
CLI and the running app can never migrate different databases. And SQLite runs in
batch mode: it cannot ALTER most things in place, so Alembic rebuilds the table
behind the scenes — which is exactly what a laptop install needs and what
PostgreSQL never triggers.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import Base
from app import models  # noqa: F401 - registers every table on Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # The app can hand in an already-built engine (see app/migrate.py); the CLI
    # falls back to the configured URL.
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        section = dict(config.get_section(config.config_ini_section) or {})
        section["sqlalchemy.url"] = _url()
        engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
        with engine.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
