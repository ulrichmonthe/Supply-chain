"""Editing in the tool: a facility, its demand, and undoing what was typed.

Every edit here is checked by the same rules an import runs, signed, and reversible
by a row rather than a deletion.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


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


def nodes(client: TestClient):
    return client.get("/api/countries/1/nodes").json()


def a_facility(client: TestClient) -> dict:
    """A seeded facility that is actually served: it has lanes and demand rows."""
    served = {e["to_code"] for e in client.get("/api/countries/1/edges").json()}
    for candidate in nodes(client):
        if candidate["level"] == 3 and candidate["catchment_population"] > 0 and candidate["code"] in served:
            if client.get(f"/api/nodes/{candidate['id']}/demand").json():
                return candidate
    raise AssertionError("no seeded facility with lanes and demand")


def latest(client: TestClient, **params) -> dict:
    return client.get("/api/countries/1/audit", params={"limit": 1, **params}).json()[0]


# --- editing a facility --------------------------------------------------------------


def test_a_field_can_be_changed_in_place_and_the_ledger_says_who(client: TestClient):
    fac = a_facility(client)
    response = client.patch(
        f"/api/nodes/{fac['id']}",
        json={"catchment_population": fac["catchment_population"] + 500, "confidence_marker": "S", "reason": "2024 census."},
        headers={"X-Author": "Provincial officer"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["node"]["catchment_population"] == fac["catchment_population"] + 500
    assert body["issues"] == []

    entry = latest(client, entity_ref=fac["code"])
    assert (entry["field"], entry["author_claim"], entry["confidence_marker"]) == ("catchment_population", "Provincial officer", "S")
    assert entry["provenance"] == "manual_override"
    assert float(entry["old_value"]) == fac["catchment_population"]


def test_moving_a_facility_into_the_sea_is_flagged_immediately(client: TestClient):
    fac = a_facility(client)
    body = client.patch(f"/api/nodes/{fac['id']}", json={"lat": -3.5, "lon": 149.0}, headers={"X-Author": "Typo"}).json()
    assert "node.offshore" in {issue["code"] for issue in body["issues"]}
    assert body["node"]["geocode_source"] == "manual", "a typed coordinate is a claim by a person"
    # put it back
    client.patch(f"/api/nodes/{fac['id']}", json={"lat": fac["lat"], "lon": fac["lon"], "geocode_source": fac["geocode_source"]})


def test_an_unchanged_value_leaves_no_ledger_row(client: TestClient):
    fac = a_facility(client)
    before = len(client.get("/api/countries/1/audit", params={"entity_ref": fac["code"], "limit": 500}).json())
    client.patch(f"/api/nodes/{fac['id']}", json={"name": fac["name"]})
    after = len(client.get("/api/countries/1/audit", params={"entity_ref": fac["code"], "limit": 500}).json())
    assert after == before


# --- adding one ---------------------------------------------------------------------


def test_a_new_facility_is_checked_like_an_import(client: TestClient):
    blocked = client.post(
        "/api/countries/1/nodes",
        json={"code": "NEW-1", "name": "Nowhere clinic", "lat": 0.0, "lon": 0.0, "catchment_population": 500},
    )
    assert blocked.status_code == 422, blocked.text
    assert "node.null_island" in {issue["code"] for issue in blocked.json()["detail"]["issues"]}

    warned = client.post(
        "/api/countries/1/nodes",
        json={"code": "NEW-0", "name": "Unpeopled clinic", "lat": -7.0, "lon": 146.5, "catchment_population": 0},
        headers={"X-Author": "Analyst"},
    )
    assert warned.status_code == 201, "a missing population is a warning, not a block -- as on import"
    assert "node.no_population" in {issue["code"] for issue in warned.json()["issues"]}

    created = client.post(
        "/api/countries/1/nodes",
        json={"code": "NEW-1", "name": "Wau clinic", "lat": -7.34, "lon": 146.72, "catchment_population": 8000, "admin1": "Morobe", "reason": "Opened in June."},
        headers={"X-Author": "Analyst"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["node"]["code"] == "NEW-1"
    assert "NEW-1" in {n["code"] for n in nodes(client)}
    assert latest(client, entity_ref="NEW-1")["field"] == "created"


def test_a_code_cannot_be_taken_twice(client: TestClient):
    again = client.post("/api/countries/1/nodes", json={"code": "NEW-1", "name": "Again", "lat": -7.3, "lon": 146.7, "catchment_population": 10})
    assert again.status_code == 409


# --- retiring and restoring -----------------------------------------------------------


def test_retiring_a_facility_takes_its_lanes_and_demand_and_keeps_its_history(client: TestClient):
    fac = a_facility(client)
    lanes_before = {e["code"] for e in client.get("/api/countries/1/edges").json() if fac["code"] in (e["from_code"], e["to_code"])}
    assert lanes_before, "pick a facility that has lanes"

    response = client.post(f"/api/nodes/{fac['id']}/retire", json={"reason": "Closed by the province."}, headers={"X-Author": "Steward"})
    assert response.status_code == 200, response.text
    assert response.json()["lanes"] == len(lanes_before)

    assert fac["code"] not in {n["code"] for n in nodes(client)}
    assert not lanes_before & {e["code"] for e in client.get("/api/countries/1/edges").json()}
    retired = client.get("/api/countries/1/nodes/retired").json()
    assert any(n["code"] == fac["code"] and n["retired_reason"] == "Closed by the province." for n in retired)
    assert latest(client, entity_ref=fac["code"])["field"] == "retired"

    restored = client.post(f"/api/nodes/{fac['id']}/restore", json={"reason": "Reopened."}, headers={"X-Author": "Steward"})
    assert restored.status_code == 200
    assert restored.json()["lanes"] == len(lanes_before)
    assert fac["code"] in {n["code"] for n in nodes(client)}
    assert lanes_before <= {e["code"] for e in client.get("/api/countries/1/edges").json()}


def test_the_national_store_cannot_be_retired(client: TestClient):
    store = next(n for n in nodes(client) if n["level"] == 0)
    assert client.post(f"/api/nodes/{store['id']}/retire", json={}).status_code == 400


def test_a_retired_facility_is_not_found_by_the_edit_endpoint(client: TestClient):
    fac = a_facility(client)
    client.post(f"/api/nodes/{fac['id']}/retire", json={"reason": "temporarily"})
    assert client.patch(f"/api/nodes/{fac['id']}", json={"name": "ghost"}).status_code == 404
    client.post(f"/api/nodes/{fac['id']}/restore", json={})


# --- demand ---------------------------------------------------------------------------


def test_demand_can_be_set_per_product_without_replacing_the_rest(client: TestClient):
    fac = a_facility(client)
    before = client.get(f"/api/nodes/{fac['id']}/demand").json()
    assert before, "seeded facilities carry demand"
    first = before[0]

    updated = client.put(
        f"/api/nodes/{fac['id']}/demand",
        json={"lines": [{"sku": first["sku"], "quantity": first["quantity"] * 2, "source": "actual", "confidence": 0.9}], "reason": "mSupply issue data."},
        headers={"X-Author": "Steward"},
    )
    assert updated.status_code == 200, updated.text
    rows = {r["sku"]: r for r in updated.json()}
    assert rows[first["sku"]]["quantity"] == first["quantity"] * 2
    assert len(rows) == len(before), "rows not named are left alone"

    entry = latest(client, entity_type="demand")
    assert entry["entity_ref"] == f"{fac['code']}/{first['sku']}/0"
    assert entry["author_claim"] == "Steward"


def test_negative_demand_is_refused(client: TestClient):
    fac = a_facility(client)
    sku = client.get(f"/api/nodes/{fac['id']}/demand").json()[0]["sku"]
    assert client.put(f"/api/nodes/{fac['id']}/demand", json={"lines": [{"sku": sku, "quantity": -1}]}).status_code == 422


# --- undo -----------------------------------------------------------------------------


def test_a_hand_made_change_can_be_reverted_by_a_row_not_a_deletion(client: TestClient):
    fac = a_facility(client)
    original = fac["catchment_population"]
    client.patch(f"/api/nodes/{fac['id']}", json={"catchment_population": original + 1000}, headers={"X-Author": "Someone"})
    entry = latest(client, entity_ref=fac["code"])
    assert entry["field"] == "catchment_population"

    response = client.post(f"/api/audit/{entry['id']}/revert", headers={"X-Author": "Reviewer"})
    assert response.status_code == 200, response.text

    assert next(n for n in nodes(client) if n["id"] == fac["id"])["catchment_population"] == original
    reversal = latest(client, entity_ref=fac["code"])
    assert reversal["reverts_id"] == entry["id"]
    assert reversal["author_claim"] == "Reviewer"
    assert client.get("/api/countries/1/audit", params={"entity_ref": fac["code"], "limit": 5}).json()[1]["status"] == "reverted"
    assert client.post(f"/api/audit/{entry['id']}/revert").status_code == 409, "already reverted"


def test_a_change_that_was_changed_again_cannot_be_reverted_out_of_order(client: TestClient):
    fac = a_facility(client)
    client.patch(f"/api/nodes/{fac['id']}", json={"name": "First rename"})
    first = latest(client, entity_ref=fac["code"])
    client.patch(f"/api/nodes/{fac['id']}", json={"name": "Second rename"})
    blocked = client.post(f"/api/audit/{first['id']}/revert")
    assert blocked.status_code == 409
    assert "changed again" in blocked.json()["detail"]
    client.patch(f"/api/nodes/{fac['id']}", json={"name": fac["name"]})


def test_an_import_entry_is_not_revertible_here(client: TestClient):
    entry = client.get("/api/countries/1/audit", params={"entity_type": "global", "limit": 1}).json()[0]
    assert client.post(f"/api/audit/{entry['id']}/revert").status_code == 400
