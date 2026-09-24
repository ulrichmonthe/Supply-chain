"""Schema migrations, and the adoption of databases that predate them.

The outage these guard against is the one the boundary and tags columns each caused:
``create_all`` ignores tables that already exist, so a new column arrived as
``no such column`` on the first query, and the fix was to delete the database. From
here on the schema moves by revision, and a database from before revisions existed
is adopted rather than abandoned.
"""

from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from app.db import Base, add_missing_columns
from app.migrate import (
    BASELINE_REVISION,
    alembic_config,
    baseline_columns,
    current_revision,
    head_revision,
    prepare_database,
)


def _columns(engine, table):
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _schema_differences(engine):
    """What autogenerate would want to change -- empty when database and models agree."""
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "render_as_batch": True}
        )
        return compare_metadata(context, Base.metadata)


# --- a fresh database ------------------------------------------------------------------


def test_an_empty_database_is_built_by_the_migrations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")

    outcome = prepare_database(engine)

    assert outcome["action"] == "created"
    assert outcome["to"] == head_revision(engine)
    assert "scenario" in inspect(engine).get_table_names()


def test_the_migrations_and_the_models_describe_the_same_schema(tmp_path):
    """If a model changes without a revision, this is the test that says so."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    prepare_database(engine)

    differences = _schema_differences(engine)

    assert differences == [], f"models and migrations disagree: {differences}"


def test_preparing_an_up_to_date_database_changes_nothing(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    prepare_database(engine)

    again = prepare_database(engine)

    assert again["action"] == "current"
    assert again["from"] == again["to"] == head_revision(engine)


# --- a database from before migrations existed -----------------------------------------


@pytest.fixture
def legacy_database(tmp_path):
    """A database as ``create_all`` left it two releases ago: tables, rows, no revision
    stamp, and missing both the tags column and every ledger column.

    Built by running the baseline revision -- which is by definition the legacy shape --
    then removing the stamp and one baseline column, so it is older than the baseline.
    """
    if sqlite3.sqlite_version_info < (3, 35):  # pragma: no cover
        pytest.skip("needs ALTER TABLE DROP COLUMN")

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    config = alembic_config(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, BASELINE_REVISION)
        finally:
            config.attributes.pop("connection", None)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO country (code, name, currency, config, boundary, created_at) "
                "VALUES ('LEG', 'Legacy', 'LEG', '{}', '{}', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO audit_entry (country_id, entity_type, entity_ref, field, new_value, "
                "provenance, confidence_marker, rationale, actor, created_at) VALUES "
                "(1, 'global', '', 'seed', 'old row', 'seed', 'S', '', 'seed', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(text("DROP TABLE alembic_version"))
        connection.execute(text('ALTER TABLE "scenario" DROP COLUMN "tags"'))
    assert "alembic_version" not in inspect(engine).get_table_names()
    return engine


def test_a_legacy_database_is_adopted_and_brought_to_head(legacy_database):
    outcome = prepare_database(legacy_database)

    assert outcome["action"] == "adopted"
    assert outcome["from"] is None
    assert outcome["to"] == head_revision(legacy_database)
    assert current_revision(legacy_database) == head_revision(legacy_database)
    assert {"tags"} <= _columns(legacy_database, "scenario")
    assert {"author_claim", "batch_id", "status", "reverts_id"} <= _columns(legacy_database, "audit_entry")


def test_adoption_keeps_every_row_and_fills_the_new_columns(legacy_database):
    prepare_database(legacy_database)

    with legacy_database.connect() as connection:
        rows = connection.execute(
            text("SELECT new_value, author_claim, status FROM audit_entry")
        ).all()
    assert rows == [("old row", "anonymous", "applied")]


def test_after_adoption_the_database_matches_the_models(legacy_database):
    prepare_database(legacy_database)
    assert _schema_differences(legacy_database) == []


def test_the_stopgap_stops_at_the_baseline_and_leaves_the_rest_to_migrations(legacy_database):
    """Adding a post-baseline column early would make its own migration fail on
    'column already exists' -- the stopgap must know where the baseline ends."""
    added = add_missing_columns(legacy_database, only=baseline_columns())

    assert "scenario.tags" in added, "a baseline column is the stopgap's job"
    assert not any(name.startswith("audit_entry.") for name in added), "ledger columns belong to revision 0002"


def test_the_baseline_columns_come_from_the_baseline_revision_itself():
    columns = baseline_columns()
    assert "tags" in columns["scenario"]
    assert "author_claim" not in columns["audit_entry"]


def test_a_database_made_by_create_all_from_current_models_is_adopted_at_head(tmp_path):
    """The other kind of unstamped database: every column already there. Stamping it at
    the baseline would make revision 0002 fail on 'column already exists'."""
    engine = create_engine(f"sqlite:///{tmp_path / 'created.db'}")
    Base.metadata.create_all(engine)

    outcome = prepare_database(engine)

    assert outcome["action"] == "adopted"
    assert outcome["to"] == head_revision(engine)
    assert _schema_differences(engine) == []
    assert prepare_database(engine)["action"] == "current"
