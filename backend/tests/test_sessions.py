"""Sessions: a named, complete save of the working state that opens back exactly, keeps
the work it replaces as a draft, and can say in words what differs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import sessions as sessions_mod
from app.db import get_session
from app.main import app
from app.models import Country, DatasetSnapshot, Demand, Node, Result, Scenario, WorkSession


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


AUTHOR = {"X-Author": "Ulrich, JSI"}


def _node(client: TestClient, code: str) -> dict:
    return next(n for n in client.get("/api/countries/1/nodes").json() if n["code"] == code)


def test_a_save_captures_every_row_and_dedups_by_content(client: TestClient, isolated_session_factory):
    client.post("/api/scenarios/1/run")
    first = client.post("/api/countries/1/sessions", json={"name": "PNG baseline", "note": "As seeded."}, headers=AUTHOR)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["kind"] == "saved" and body["author_claim"] == "Ulrich, JSI" and body["is_current"] is True
    assert body["changes_since"] == 0
    assert body["summary"]["facilities"] == 134 and body["summary"]["scenarios"] == 7 and body["summary"]["results"] == 1
    assert body["size_bytes"] > 100_000

    # Saving the same state again shares the snapshot: the hash is the identity.
    second = client.post("/api/countries/1/sessions", json={"name": "Same again"}, headers=AUTHOR).json()
    session = isolated_session_factory()
    try:
        a = session.get(WorkSession, body["id"])
        b = session.get(WorkSession, second["id"])
        assert a.snapshot_id != b.snapshot_id or True  # the save itself writes a ledger row, so...
        # ...content differs only in nothing model-side: the snapshot rows must be equal.
        assert a.snapshot.sha256 == b.snapshot.sha256
        assert a.snapshot_id == b.snapshot_id
    finally:
        session.close()


def test_changes_since_counts_the_ledger_after_the_save(client: TestClient):
    saved = client.post("/api/countries/1/sessions", json={"name": "Before edits"}, headers=AUTHOR).json()
    node = [n for n in client.get("/api/countries/1/nodes").json() if n["level"] == 3][5]
    client.patch(f"/api/nodes/{node['id']}", json={"catchment_population": (node["catchment_population"] or 1000) + 500, "reason": "census"}, headers=AUTHOR)
    shelf = client.get("/api/countries/1/sessions").json()
    assert shelf["current"]["session_id"] == saved["id"]
    assert shelf["current"]["changes_since"] >= 1


def test_opening_a_session_restores_it_exactly_and_drafts_the_work_it_replaces(client: TestClient, isolated_session_factory):
    client.post("/api/scenarios/1/run")
    nodes = [n for n in client.get("/api/countries/1/nodes").json() if n["level"] == 3 and n["catchment_population"]]
    node = nodes[7]
    original_population = node["catchment_population"]
    saved = client.post("/api/countries/1/sessions", json={"name": "Checkpoint"}, headers=AUTHOR).json()
    baseline_result_before = client.get("/api/countries/1/scenarios").json()[0]["latest_result_id"]

    # Work on: edit a population (estimates follow), retire a facility, add a scenario.
    client.patch(f"/api/nodes/{node['id']}", json={"catchment_population": original_population * 2, "reason": "test"}, headers=AUTHOR)
    client.post(f"/api/nodes/{nodes[9]['id']}/retire", json={"reason": "closed"}, headers=AUTHOR)
    assert client.post("/api/scenarios/1/clone", json={"name": "Scratch"}, headers=AUTHOR).status_code == 201
    assert len(client.get("/api/countries/1/scenarios").json()) == 8
    assert _node(client, node["code"])["catchment_population"] == original_population * 2

    opened = client.post(f"/api/sessions/{saved['id']}/open", headers=AUTHOR).json()
    assert opened["restored"]["nodes"] == 143 and opened["restored"]["scenarios"] == 7
    assert opened["draft"] is not None and opened["draft"]["kind"] == "draft"
    assert opened["draft"]["name"].startswith("Draft before reopening")
    assert opened["current"]["session_id"] == saved["id"] and opened["current"]["changes_since"] == 0

    # Exactly as saved: the population, the estimate that followed it, the retired facility, the scenarios.
    assert _node(client, node["code"])["catchment_population"] == original_population
    assert len(client.get("/api/countries/1/scenarios").json()) == 7
    assert not any(n["code"] == nodes[9]["code"] for n in client.get("/api/countries/1/nodes/retired").json())
    assert any(n["code"] == nodes[9]["code"] for n in client.get("/api/countries/1/nodes").json())
    demand = client.get(f"/api/nodes/{node['id']}/demand").json()
    assert all(row["derivation"] for row in demand if row["quantity"]), "estimates stay live through a restore"
    # The result came back with the id it had, so the scorecard still points at it.
    assert client.get("/api/countries/1/scenarios").json()[0]["latest_result_id"] == baseline_result_before

    # The draft holds the work that was replaced, and can be opened back.
    diff = client.get(f"/api/sessions/{opened['draft']['id']}/diff", params={"against": saved["id"]}).json()
    text = " ".join(diff["sentences"])
    # Read from the draft towards the checkpoint: the retired facility comes back.
    assert "Population changed at 1 facility" in text
    assert "Estimates moved with their inputs at 1 facility" in text
    assert "1 facility added" in text
    assert "Scenario “Scratch” removed" in text

    reopened = client.post(f"/api/sessions/{opened['draft']['id']}/open", headers=AUTHOR).json()
    assert reopened["draft"] is None, "nothing changed since the checkpoint was opened, so no new draft"
    assert _node(client, node["code"])["catchment_population"] == original_population * 2
    assert len(client.get("/api/countries/1/scenarios").json()) == 8

    # The ledger says what happened, and by whom.
    audit = client.get("/api/countries/1/audit").json()
    opened_rows = [row for row in audit if row.get("entity_type") == "session" and row.get("field") == "opened"]
    assert len(opened_rows) >= 2 and opened_rows[0]["author_claim"] == "Ulrich, JSI"


def test_the_diff_reads_scenario_and_result_changes(client: TestClient):
    client.post("/api/scenarios/1/run")
    saved = client.post("/api/countries/1/sessions", json={"name": "Before levers"}, headers=AUTHOR).json()
    scenario = client.get("/api/countries/1/scenarios").json()[0]
    client.patch(
        "/api/scenarios/1",
        json={
            "levers": {**scenario["levers"], "month": 3},
            "objective_weights": {**scenario["objective_weights"], "equity": 0.9},
        },
        headers=AUTHOR,
    )
    client.post("/api/scenarios/1/run")
    diff = client.get(f"/api/sessions/{saved['id']}/diff").json()
    text = " ".join(diff["sentences"])
    assert diff["to"] == "the working state" and diff["same"] is False
    assert "month blank → 3" in text
    assert "equity 0.5 → 0.9" in text
    assert "annual cost" in text


def test_nothing_differs_reads_as_such(client: TestClient):
    saved = client.post("/api/countries/1/sessions", json={"name": "Still"}, headers=AUTHOR).json()
    diff = client.get(f"/api/sessions/{saved['id']}/diff").json()
    assert diff["same"] is True
    assert diff["sentences"] == ["Nothing differs. The two are the same working state."]


def test_rename_and_delete(client: TestClient, isolated_session_factory):
    saved = client.post("/api/countries/1/sessions", json={"name": "Temp"}, headers=AUTHOR).json()
    renamed = client.patch(f"/api/sessions/{saved['id']}", json={"name": "Kept", "note": "worth keeping"}).json()
    assert renamed["name"] == "Kept" and renamed["note"] == "worth keeping"
    assert client.patch(f"/api/sessions/{saved['id']}", json={"name": "   "}).status_code == 400

    snapshot_id = None
    session = isolated_session_factory()
    try:
        snapshot_id = session.get(WorkSession, saved["id"]).snapshot_id
    finally:
        session.close()
    assert client.delete(f"/api/sessions/{saved['id']}").status_code == 204
    assert client.get(f"/api/sessions/{saved['id']}").status_code == 404
    shelf = client.get("/api/countries/1/sessions").json()
    assert shelf["current"] is None, "deleting the session you were working from clears the pointer"
    session = isolated_session_factory()
    try:
        assert session.get(DatasetSnapshot, snapshot_id) is None or session.scalar(
            select(WorkSession).where(WorkSession.snapshot_id == snapshot_id)
        ) is not None
    finally:
        session.close()


def test_a_named_draft_becomes_a_save(client: TestClient):
    a = client.post("/api/countries/1/sessions", json={"name": "A"}, headers=AUTHOR).json()
    nodes = [n for n in client.get("/api/countries/1/nodes").json() if n["level"] == 3]
    client.patch(f"/api/nodes/{nodes[3]['id']}", json={"catchment_population": 99999, "reason": "x"}, headers=AUTHOR)
    opened = client.post(f"/api/sessions/{a['id']}/open", headers=AUTHOR).json()
    draft = opened["draft"]
    assert draft["kind"] == "draft"
    named = client.patch(f"/api/sessions/{draft['id']}", json={"name": "The 99,999 idea"}).json()
    assert named["kind"] == "saved"


def test_capture_and_restore_round_trip_every_column(isolated_session_factory):
    session = isolated_session_factory()
    try:
        country = session.get(Country, 1)
        before = sessions_mod.capture(session, country)
        counts = sessions_mod.restore(session, 1, before)
        session.commit()
        after = sessions_mod.capture(session, session.get(Country, 1))
        assert counts["nodes"] == len(before["nodes"])
        assert sessions_mod.digest(before) == sessions_mod.digest(after)
        # Relationships still resolve after a restore by id.
        node = session.scalars(select(Node).where(Node.level == 3).limit(1)).first()
        demand = session.scalars(select(Demand).where(Demand.node_id == node.id)).all()
        assert demand and all(d.product_id for d in demand)
        scenario = session.scalars(select(Scenario).where(Scenario.is_baseline.is_(True))).first()
        assert scenario is not None and scenario.country_id == 1
    finally:
        session.rollback()
        session.close()
