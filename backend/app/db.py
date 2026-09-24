from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Optional

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

logger = logging.getLogger("hscn")

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver glue
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def add_missing_columns(target=None, only: "Optional[dict]" = None) -> list:
    """Add columns the models declare and the database does not yet have.

    ``create_all`` creates missing tables and silently ignores tables that already
    exist, so a new column on an existing model reaches a running instance as
    ``no such column`` on the first query — an outage, not a warning. This closes
    that specific gap and nothing else.

    It is a stopgap, not migrations, and the difference matters. It can only ADD a
    column: it will not rename one, drop one, change a type, add a constraint, or
    backfill anything it cannot derive. Existing rows get NULL, except for JSON
    columns, which are filled with an empty object or list so ``row.field or {}``
    is not the only thing standing between the app and a crash. Anything beyond
    that still needs a real migration tool.

    ``target`` is the engine to reconcile, defaulting to the application's own.
    ``only`` restricts what may be added to ``{table: {column, ...}}`` -- adoption of a
    pre-migration database passes the baseline revision's columns here, so the stopgap
    brings the database to the baseline and no further, leaving later columns to the
    migrations that own them.

    Returns the columns it added, as "table.column" strings, so startup can say so.
    """
    target = target or engine
    inspector = inspect(target)
    existing_tables = set(inspector.get_table_names())
    added = []

    with target.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all makes whole tables; this is only for new columns
            have = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                if only is not None and column.name not in only.get(table.name, set()):
                    continue
                ddl = column.type.compile(target.dialect)
                blank = _blank_json(column)
                # A NOT NULL column needs a default the database can apply to the rows
                # already there; without one it would be added nullable and drift from
                # the model for the rest of its life.
                if not column.nullable and blank is not None:
                    ddl += " NOT NULL DEFAULT " + _literal(blank)
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}')
                )
                added.append(f"{table.name}.{column.name}")

    if added:
        logger.warning("Added missing columns: %s. This is a stopgap for migrations.", ", ".join(added))
    return added


def _literal(value: str) -> str:
    """A single-quoted SQL string literal; only ever fed the JSON blanks above."""
    return "'" + value.replace("'", "''") + "'"


def _blank_json(column) -> Optional[str]:
    """The empty value for a JSON column, taken from the model's own default.

    SQLAlchemy wraps a callable default (``default=list``) in a context-aware shim, so
    the callable stored here is not the ``list`` that was written in the model. Calling
    it is the only reliable way to find out what it produces.
    """
    if column.default is None:
        return None
    arg = column.default.arg
    if callable(arg):
        try:
            value = arg(None)  # the wrapped form takes an execution context
        except TypeError:  # pragma: no cover - a plain zero-argument callable
            value = arg()
    else:
        value = arg
    return json.dumps(value) if isinstance(value, (list, dict)) else None
