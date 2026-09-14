"""Tagging scenarios, and the filing that depends on it staying tidy.

A tag is only worth having if filtering on it is trustworthy, and the way that fails
is quiet: three spellings of "ministerial" and a filter that shows a third of what it
should. So most of what is checked here is the cleaning, not the storing.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, add_missing_columns, get_session
from app.main import app
from app.models import Country, Scenario
from app.tagging import MAX_LENGTH, MAX_TAGS, normalise_tags


@pytest.fixture
def client(isolated_session_factory):
    def _session_override():
        session = isolated_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- cleaning ------------------------------------------------------------------------


def test_the_same_tag_typed_three_ways_is_one_tag():
    assert normalise_tags(["Ministerial", "ministerial", "  MINISTERIAL  "]) == ["Ministerial"]


def test_the_first_spelling_is_the_one_kept():
    """The tag belongs to whoever typed it; only the matching is ours."""
    assert normalise_tags(["Q3 Review", "q3 review"]) == ["Q3 Review"]
    assert normalise_tags(["q3 review", "Q3 Review"]) == ["q3 review"]


def test_whitespace_is_collapsed_rather_than_preserved():
    assert normalise_tags(["  board   pack  "]) == ["board pack"]


def test_blank_tags_are_dropped_instead_of_stored():
    assert normalise_tags(["", "   ", "\t", "real"]) == ["real"]


def test_commas_cannot_smuggle_a_second_tag_into_one_label():
    """The interface splits on comma; a tag containing one would filter as itself only."""
    assert normalise_tags(["cost,equity"]) == ["cost equity"]


def test_tags_are_bounded_in_length_and_number():
    assert len(normalise_tags(["x" * 200])[0]) == MAX_LENGTH
    assert len(normalise_tags([f"tag-{i}" for i in range(40)])) == MAX_TAGS


def test_nothing_at_all_is_an_empty_list_not_a_crash():
    for empty in (None, [], ""):
        assert normalise_tags(empty) == []


# --- through the API -----------------------------------------------------------------


def test_the_seeded_workspace_arrives_already_filed(client: TestClient):
    """An empty tag filter teaches nobody what tags are for."""
    scenarios = client.get("/api/countries/1/scenarios").json()
    assert all("tags" in s for s in scenarios)
    tagged = [s for s in scenarios if s["tags"]]
    assert len(tagged) == len(scenarios), "every seeded scenario carries at least one tag"
    assert "board pack" in {tag for s in scenarios for tag in s["tags"]}


def test_a_tag_can_be_added_and_removed(client: TestClient):
    added = client.patch("/api/scenarios/2", json={"tags": ["Ministerial", "cost"]})
    assert added.status_code == 200
    assert added.json()["tags"] == ["Ministerial", "cost"]

    cleared = client.patch("/api/scenarios/2", json={"tags": []})
    assert cleared.json()["tags"] == [], "an empty list must clear the tags, not be ignored"


def test_tags_are_cleaned_on_the_way_in_not_only_on_the_way_out(client: TestClient):
    body = client.patch("/api/scenarios/2", json={"tags": ["  Board  Pack ", "board pack"]}).json()
    assert body["tags"] == ["Board Pack"]
    assert client.get("/api/scenarios/2").json()["tags"] == ["Board Pack"], "stored clean, not just shown clean"


def test_patching_something_else_leaves_the_tags_alone(client: TestClient):
    client.patch("/api/scenarios/2", json={"tags": ["keep me"]})
    client.patch("/api/scenarios/2", json={"description": "A new description."})
    assert client.get("/api/scenarios/2").json()["tags"] == ["keep me"]


def test_a_duplicate_inherits_the_filing(client: TestClient):
    client.patch("/api/scenarios/2", json={"tags": ["ministerial", "cost"]})
    clone = client.post("/api/scenarios/2/clone", json={"name": "Variant for the minister"}).json()
    assert clone["tags"] == ["ministerial", "cost"]


def test_a_duplicate_can_be_filed_somewhere_else(client: TestClient):
    client.patch("/api/scenarios/2", json={"tags": ["ministerial"]})
    clone = client.post(
        "/api/scenarios/2/clone", json={"name": "Internal working copy", "tags": ["draft"]}
    ).json()
    assert clone["tags"] == ["draft"]


def test_a_new_scenario_can_be_filed_as_it_is_created(client: TestClient):
    created = client.post(
        "/api/countries/1/scenarios",
        json={"name": "Fuel shock", "tags": ["risk", "RISK", "  "]},
    ).json()
    assert created["tags"] == ["risk"]


def test_the_list_can_be_filtered_by_tag_ignoring_case(client: TestClient):
    # A tag nothing else in this module uses, because the database is shared across it
    # and a duplicate made by an earlier test would otherwise answer this one.
    client.patch("/api/scenarios/2", json={"tags": ["Cabinet Submission"]})
    client.patch("/api/scenarios/3", json={"tags": ["cabinet submission"]})
    client.patch("/api/scenarios/4", json={"tags": ["internal"]})

    filtered = client.get("/api/countries/1/scenarios", params={"tag": "CABINET SUBMISSION"}).json()
    assert {s["id"] for s in filtered} == {2, 3}

    assert client.get("/api/countries/1/scenarios", params={"tag": "nobody uses this"}).json() == []


def test_the_filter_narrows_the_list_rather_than_replacing_it(client: TestClient):
    client.patch("/api/scenarios/5", json={"tags": ["one scenario only"]})
    everything = client.get("/api/countries/1/scenarios").json()
    narrowed = client.get("/api/countries/1/scenarios", params={"tag": "one scenario only"}).json()

    assert {s["id"] for s in everything} >= {1, 2, 3, 4, 5, 6, 7}, "the seeded set is all still there"
    assert [s["id"] for s in narrowed] == [5]
    assert len(everything) > len(narrowed)


# --- the column arriving on a database that predates it ------------------------------


@pytest.fixture
def older_database(tmp_path):
    """A database built with the current models, then aged by removing a column.

    Standing in for the real situation: an instance that has been running since before
    the column existed. Nothing is seeded — this is about the schema, and seeding costs
    a second the test does not need to spend.
    """
    if sqlite3.sqlite_version_info < (3, 35):  # pragma: no cover - old interpreter
        pytest.skip(f"ALTER TABLE DROP COLUMN needs SQLite 3.35; this is {sqlite3.sqlite_version}")

    engine = create_engine(f"sqlite:///{tmp_path / 'older.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    country = Country(code="TST", name="Testland", currency="TST")
    session.add(country)
    session.flush()
    session.add(Scenario(country_id=country.id, name="Already here", is_baseline=True))
    session.commit()
    session.close()

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE scenario DROP COLUMN tags"))
    return engine


def test_a_missing_column_is_added_rather_than_crashing_every_query(older_database):
    """The failure this prevents is an outage: 'no such column: scenario.tags'.

    ``create_all`` skips tables it already sees, so a new column never reaches an
    existing database. Deleting the file is fine on a laptop and is data loss anywhere
    else, which is exactly what the country boundary column cost.
    """
    assert "tags" not in {c["name"] for c in inspect(older_database).get_columns("scenario")}

    added = add_missing_columns(older_database)

    assert "scenario.tags" in added
    assert "tags" in {c["name"] for c in inspect(older_database).get_columns("scenario")}


def test_rows_that_predate_a_json_column_read_as_empty_not_null(older_database):
    """A NULL here would make `scenario.tags` fail the response model on read."""
    add_missing_columns(older_database)
    with older_database.connect() as connection:
        stored = connection.execute(text("SELECT tags FROM scenario")).scalars().all()
    assert stored == ["[]"]


def test_reconciling_twice_changes_nothing_the_second_time(older_database):
    add_missing_columns(older_database)
    assert add_missing_columns(older_database) == []
