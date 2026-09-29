"""The table editor: every table listed with its retired rows, lanes and products
editable and retirable like facilities, and one field set across many rows at once."""

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


AUTHOR = {"X-Author": "Ulrich, JSI"}


def test_every_table_lists_with_retired_rows_marked(client: TestClient):
    for table in ("nodes", "edges", "products", "demand"):
        rows = client.get(f"/api/countries/1/tables/{table}").json()
        assert rows and "id" in rows[0]
    assert client.get("/api/countries/1/tables/unicorns").status_code == 404
    demand = client.get("/api/countries/1/tables/demand").json()
    assert demand[0]["node_code"] and demand[0]["sku"] and "node_name" in demand[0]


def test_a_lane_can_be_added_edited_retired_and_restored(client: TestClient):
    nodes = client.get("/api/countries/1/nodes").json()
    hub = next(n for n in nodes if n["level"] == 1)
    clinic = next(n for n in nodes if n["level"] == 3 and n["admin1"] == hub["admin1"]) if any(
        n["level"] == 3 and n["admin1"] == hub["admin1"] for n in nodes
    ) else next(n for n in nodes if n["level"] == 3)
    created = client.post(
        "/api/countries/1/edges",
        json={"code": "TEST-LANE", "from_code": hub["code"], "to_code": clinic["code"], "mode": "road", "reason": "field visit"},
        headers=AUTHOR,
    )
    assert created.status_code == 201, created.text
    lane = created.json()
    assert lane["distance_km"] > 0 and lane["distance_method"] in ("detour_factor", "osrm", "great_circle")
    assert client.post("/api/countries/1/edges", json={"code": "TEST-LANE", "from_code": hub["code"], "to_code": clinic["code"]}, headers=AUTHOR).status_code == 409

    edited = client.patch(f"/api/edges/{lane['id']}", json={"mode": "sea", "service_frequency": "WEEKLY", "capacity_per_trip_m3": 12.5, "rationale": "coastal run"}, headers=AUTHOR)
    assert edited.status_code == 200, edited.text
    assert edited.json()["mode"] == "sea" and edited.json()["service_frequency"] == "WEEKLY"

    assert client.post(f"/api/edges/{lane['id']}/retire", json={"reason": "bridge down"}, headers=AUTHOR).json()["retired"] == "TEST-LANE"
    listed = {e["code"]: e for e in client.get("/api/countries/1/tables/edges").json()}
    assert listed["TEST-LANE"]["retired_at"] is not None
    assert not any(e["code"] == "TEST-LANE" for e in client.get("/api/countries/1/edges").json()), "retired lanes vanish from the model"
    restored = client.post(f"/api/edges/{lane['id']}/restore", json={"reason": "bridge rebuilt"}, headers=AUTHOR).json()
    assert restored["retired_at"] is None
    audit = client.get("/api/countries/1/audit", params={"entity_ref": "TEST-LANE"}).json()
    fields = {row["field"] for row in audit}
    assert {"created", "mode", "retired", "restored"} <= fields and audit[0]["author_claim"] == "Ulrich, JSI"


def test_a_product_can_be_added_edited_and_retired_with_its_demand(client: TestClient):
    created = client.post(
        "/api/countries/1/products",
        json={"sku": "TEST-SKU", "name": "Test kit", "temperature_band": "+2-8", "volume_per_unit_cm3": 1200, "reason": "new programme"},
        headers=AUTHOR,
    )
    assert created.status_code == 201, created.text
    product = created.json()
    assert client.patch(f"/api/products/{product['id']}", json={"unit_cost": 4.5}, headers=AUTHOR).json()["unit_cost"] == 4.5
    # Give it demand at one facility, then retire the product: the demand goes with it.
    clinic = next(n for n in client.get("/api/countries/1/nodes").json() if n["level"] == 3)
    client.put(f"/api/nodes/{clinic['id']}/demand", json={"lines": [{"sku": "TEST-SKU", "quantity": 40}]}, headers=AUTHOR)
    retired = client.post(f"/api/products/{product['id']}/retire", json={"reason": "programme ended"}, headers=AUTHOR).json()
    assert retired["demand_rows"] == 1
    assert not any(p["sku"] == "TEST-SKU" for p in client.get("/api/countries/1/products").json())
    assert not any(r["sku"] == "TEST-SKU" for r in client.get(f"/api/nodes/{clinic['id']}/demand").json())
    brought = client.post(f"/api/products/{product['id']}/restore", json={}, headers=AUTHOR).json()
    assert brought["demand_rows"] == 1
    assert any(r["sku"] == "TEST-SKU" and r["quantity"] == 40 for r in client.get(f"/api/nodes/{clinic['id']}/demand").json())


def test_one_field_across_many_rows_is_one_batch_with_a_row_each(client: TestClient):
    nodes = [n for n in client.get("/api/countries/1/nodes").json() if n["level"] == 3][:5]
    ids = [n["id"] for n in nodes]
    outcome = client.post(
        "/api/countries/1/tables/nodes/bulk",
        json={"ids": ids, "field": "operating_status", "value": "non_operational", "reason": "storm damage", "confidence_marker": "S"},
        headers=AUTHOR,
    ).json()
    assert outcome["changed"] == 5 and outcome["of"] == 5
    after = {n["id"]: n for n in client.get("/api/countries/1/nodes").json()}
    assert all(after[i]["operating_status"] == "non_operational" for i in ids)
    rows = client.get("/api/countries/1/audit", params={"batch_id": outcome["batch_id"]}).json()
    assert len(rows) == 5 and all(r["confidence_marker"] == "S" and r["rationale"] == "storm damage" for r in rows)
    # Reverting one row of the batch reverts only that row.
    client.post(f"/api/audit/{rows[0]['id']}/revert", headers=AUTHOR)
    after = {n["id"]: n for n in client.get("/api/countries/1/nodes").json()}
    assert sum(1 for i in ids if after[i]["operating_status"] == "operational") == 1

    # Population in bulk moves the estimates that read it.
    again = client.post("/api/countries/1/tables/nodes/bulk", json={"ids": ids[:2], "field": "catchment_population", "value": 50000}, headers=AUTHOR).json()
    assert again["changed"] == 2 and again["recomputed"] > 0
    refused = client.post("/api/countries/1/tables/nodes/bulk", json={"ids": ids, "field": "code", "value": "X"}, headers=AUTHOR)
    assert refused.status_code == 400


def test_demand_rows_edited_from_the_grid_are_pinned(client: TestClient):
    rows = client.get("/api/countries/1/tables/demand").json()
    row = next(r for r in rows if r["derivation"])
    updated = client.put(f"/api/demand/{row['id']}", json={"sku": row["sku"], "quantity": 999}, headers=AUTHOR).json()
    pinned = next(r for r in updated if r["sku"] == row["sku"])
    assert pinned["quantity"] == 999 and pinned["derivation"] is None
    bulk = client.post("/api/countries/1/tables/demand/bulk", json={"ids": [rows[1]["id"], rows[2]["id"]], "field": "quantity", "value": 12}, headers=AUTHOR).json()
    assert bulk["changed"] == 2
    after = {r["id"]: r for r in client.get("/api/countries/1/tables/demand").json()}
    assert after[rows[1]["id"]]["quantity"] == 12 and after[rows[1]["id"]]["derivation"] is None
