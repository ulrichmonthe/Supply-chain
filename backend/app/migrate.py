"""Bringing a database up to the schema the code expects, at startup.

Three situations arrive here, and each gets a different answer:

* An empty database (first run on a laptop): apply every migration from the start.
* A database from before migrations existed -- it has tables but no
  ``alembic_version``: it is adopted. The column stopgap brings it to the shape of the
  baseline revision, it is stamped as that revision, and the rest of the migrations
  run on top. Nothing is dropped and nobody has to delete a file.
* A migrated database: upgrade to head, which is usually a no-op.

Adoption is the only place ``add_missing_columns`` is still used. Every schema change
from here on is a revision under ``backend/migrations/versions``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from .db import add_missing_columns, engine as default_engine

logger = logging.getLogger("hscn")

BACKEND_DIR = Path(__file__).resolve().parent.parent
#: The revision a pre-migration database is treated as being at, once the column
#: stopgap has run. It is the schema at the commit migrations were introduced.
BASELINE_REVISION = "0001"


def alembic_config(target: Engine) -> Config:
    """An Alembic configuration bound to ``target`` rather than to whatever alembic.ini
    or the environment says -- so tests and the app migrate the database they mean to."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", str(target.url).replace("%", "%%"))
    return config


def current_revision(target: Engine) -> Optional[str]:
    with target.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision(target: Engine) -> str:
    script = ScriptDirectory.from_config(alembic_config(target))
    return script.get_current_head() or ""


def columns_at(revision: str) -> dict:
    """``{table: {column, ...}}`` as the schema stands at ``revision``.

    Built by running the migrations up to that revision into a throwaway in-memory
    database and reading it back, so the migration files -- not a hand-kept list --
    decide what any revision's schema is.
    """
    scratch = create_engine("sqlite://")
    config = alembic_config(scratch)
    with scratch.begin() as connection:
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, revision)
        finally:
            config.attributes.pop("connection", None)
    inspector = inspect(scratch)
    return {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
        if table != "alembic_version"
    }


def baseline_columns() -> dict:
    return columns_at(BASELINE_REVISION)


def _revisions_base_to_head(target: Engine) -> list:
    script = ScriptDirectory.from_config(alembic_config(target))
    return [rev.revision for rev in reversed(list(script.walk_revisions()))]


def _covers(schema: dict, have: dict) -> bool:
    """Every column ``schema`` describes is present in ``have``."""
    return all(columns <= have.get(table, set()) for table, columns in schema.items())


def adoption_revision(target: Engine) -> str:
    """The revision a pre-migration database most resembles.

    Usually the baseline. But a database made by ``create_all`` from newer models --
    which has every current column and no stamp -- must be stamped at head, or the
    migrations that add those columns would fail on "already exists". So: the latest
    revision whose columns are all already there.
    """
    inspector = inspect(target)
    have = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }
    matched = BASELINE_REVISION
    for revision in _revisions_base_to_head(target):
        if _covers(columns_at(revision), have):
            matched = revision
        else:
            break
    return matched


def prepare_database(target: Optional[Engine] = None) -> dict:
    """Make ``target`` match the models, and say what was done.

    Returns ``{"action": "created" | "adopted" | "upgraded" | "current", "from": ..., "to": ...}``.
    """
    target = target or default_engine
    config = alembic_config(target)
    tables = set(inspect(target).get_table_names())
    before = current_revision(target) if "alembic_version" in tables else None
    head = head_revision(target)

    if not tables:
        action = "created"
        _upgrade(config, target)
    elif before is None:
        # Pre-migration database. The stopgap can only add columns, which is exactly
        # the gap between any older create_all() schema and the baseline revision.
        stamp = adoption_revision(target)
        added = add_missing_columns(target, only=columns_at(stamp))
        if added:
            logger.info("Adopting a pre-migration database: added %s.", ", ".join(added))
        with target.begin() as connection:
            config.attributes["connection"] = connection
            try:
                command.stamp(config, stamp)
            finally:
                config.attributes.pop("connection", None)
        action = "adopted"
        _upgrade(config, target)
    elif before != head:
        action = "upgraded"
        _upgrade(config, target)
    else:
        action = "current"

    after = current_revision(target)
    if action != "current":
        logger.info("Database %s: revision %s -> %s.", action, before, after)
    return {"action": action, "from": before, "to": after}


def _upgrade(config: Config, target: Engine) -> None:
    # Hand Alembic an open connection so it uses this engine's dialect settings (the
    # SQLite pragmas, batch mode) rather than building its own from the URL.
    with target.begin() as connection:
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, "head")
        finally:
            config.attributes.pop("connection", None)
