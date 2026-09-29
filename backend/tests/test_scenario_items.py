"""Scenario items: data changes a scenario carries and applies on top of the baseline
when it runs, without touching the base data."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.engine import overlay
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


def _clone(client, name, items):
    scenario = client.post("/api/scenarios/1/clone", json={"name": name}, headers=AUTHOR).json()
    response = client.patch(f"/api/scenarios/{scenario['id']}", json={"data_items": items}, headers=AUTHOR)
    assert response.status_code == 200, response.text
    return response.json()


def test_items_are_described_in_words():
    assert overlay.describe({"kind": "close_facility", "code": "X"}, {"X": "Wewak"}) == "close Wewak"
    assert overlay.describe({"kind": "scale_demand", "factor": 1.2, "admin1": "Morobe"}) == "demand × 1.2 in Morobe"
    assert overlay.describe({"kind": "add_lane", "from_code": "A", "to_code": "B", "mode": "sea"}) == "add lane A → B by sea"


def test_unknown_codes_and_fields_are_refused(client: TestClient):
    scenario = client.post("/api/scenarios/1/clone", json={"name": "Bad items"}, headers=AUTHOR).json()
    bad = client.patch(f"/api/scenarios/{scenario['id']}", json={"data_items": [{"kind": "close_facility", "code": "NOPE"}]}, headers=AUTHOR)
    assert bad.status_code == 400 and "no facility with code 'NOPE'" in bad.json()["detail"]
    bad = client.patch(f"/api/scenarios/{scenario['id']}", json={"data_items": [{"kind": "set_lane_field", "code": "NOPE", "field": "mode", "value": "sea"}]}, headers=AUTHOR)
    assert bad.status_code == 400
    bad = client.patch(f"/api/scenarios/{scenario['id']}", json={"data_items": [{"kind": "set_node_field", "code": "PNG-MOR-001", "field": "id", "value": 9}]}, headers=AUTHOR)
    assert bad.status_code == 400 and "not a field" in bad.json()["detail"]


def test_scaling_demand_changes_the_run_but_not_the_base(client: TestClient):
    base = client.post("/api/scenarios/1/run").json()
    before_rows = client.get("/api/countries/1/tables/demand").json()
    scenario = _clone(client, "Demand up a fifth", [{"kind": "scale_demand", "factor": 1.2}])
    result = client.post(f"/api/scenarios/{scenario['id']}/run").json()
    assert result["status"] == "ok"
    assert abs(result["kpi_set"]["delivered_m3"] + result["kpi_set"]["unmet_m3"] - 1.2 * (base["kpi_set"]["delivered_m3"] + base["kpi_set"]["unmet_m3"])) < 1.0
    after_rows = client.get("/api/countries/1/tables/demand").json()
    assert [r["quantity"] for r in after_rows] == [r["quantity"] for r in before_rows], "the base data is untouched"
    # The ledger records the items as one change to the scenario, in words.
    audit = client.get("/api/countries/1/audit", params={"entity_ref": scenario["name"]}).json()
    assert any(row["field"] == "data_items" and "demand × 1.2" in (row["new_value"] or "") for row in audit)


def test_closing_a_facility_takes_its_demand_and_lanes_out_of_the_run(client: TestClient):
    base = client.post("/api/scenarios/1/run").json()
    clinic = next(n for n in client.get("/api/countries/1/nodes").json() if n["level"] == 3 and n["catchment_population"] > 0)
    scenario = _clone(client, "Close one clinic", [{"kind": "close_facility", "code": clinic["code"]}])
    result = client.post(f"/api/scenarios/{scenario['id']}/run").json()
    detail = client.get(f"/api/results/{result['id']}").json()
    assert not any(n["code"] == clinic["code"] for n in detail["per_node_detail"])
    assert len(detail["per_node_detail"]) == len(client.get(f"/api/results/{base['id']}").json()["per_node_detail"]) - 1
    assert any(n["code"] == clinic["code"] for n in client.get("/api/countries/1/nodes").json()), "still in the base"


def test_a_new_store_with_lanes_can_be_opened_inside_a_scenario(client: TestClient):
    nodes = client.get("/api/countries/1/nodes").json()
    national = next(n for n in nodes if n["level"] == 0)
    clinics = [n for n in nodes if n["level"] == 3 and n["admin1"] == "Morobe"][:6]
    assert clinics, "the seed has Morobe clinics"
    items = [
        {"kind": "add_node", "code": "NEW-STORE", "name": "Kimbe Area Medical Store", "lat": -5.55, "lon": 150.15, "level": 1,
         "hub_capable": True, "hub_fixed_cost": 150000, "hub_open_capex": 900000, "hub_throughput_m3": 3000, "operating_status": "planned"},
        {"kind": "add_lane", "code": "NAT-NEW", "from_code": national["code"], "to_code": "NEW-STORE", "mode": "sea", "cost_per_m3": 120},
    ] + [
        {"kind": "add_lane", "code": f"NEW-{c['code']}", "from_code": "NEW-STORE", "to_code": c["code"], "mode": "road", "cost_per_m3": 40}
        for c in clinics
    ]
    scenario = client.post("/api/scenarios/1/clone", json={"name": "Open Kimbe"}, headers=AUTHOR).json()
    updated = client.patch(
        f"/api/scenarios/{scenario['id']}",
        json={"data_items": items, "levers": {**scenario["levers"], "hub_nodes_open": ["NEW-STORE"]}},
        headers=AUTHOR,
    )
    assert updated.status_code == 200, updated.text
    result = client.post(f"/api/scenarios/{scenario['id']}/run").json()
    assert result["status"] == "ok"
    detail = client.get(f"/api/results/{result['id']}").json()
    assert "NEW-STORE" in detail["solver_log"]["hubs_open_codes"]
    assert any(f["hub_code"] == "NEW-STORE" for f in detail["per_edge_flow"]), "the new store supplies somebody"
    assert not any(n["code"] == "NEW-STORE" for n in client.get("/api/countries/1/nodes").json())
    # The study's lever diff lists the data items beside the levers.
    study = client.post("/api/countries/1/studies", json={"question": "Kimbe?", "scenario_ids": [scenario["id"]]}, headers=AUTHOR).json()
    diff = client.get(f"/api/studies/{study['id']}/compare").json()["lever_diff"][str(scenario["id"])]
    texts = [d["text"] for d in diff["differences"]]
    assert any(t.startswith("data: add store Kimbe") for t in texts)
    assert sum(1 for t in texts if t.startswith("data: add lane")) == 7


def test_removing_and_changing_lanes(client: TestClient):
    edges = client.get("/api/countries/1/edges").json()
    sea = next(e for e in edges if e["mode"] == "sea")
    scenario = _clone(client, "No sea run", [
        {"kind": "remove_lane", "code": sea["code"]},
        {"kind": "set_lane_field", "code": edges[0]["code"], "field": "cost_per_m3", "value": 999.0},
    ])
    result = client.post(f"/api/scenarios/{scenario['id']}/run").json()
    detail = client.get(f"/api/results/{result['id']}").json()
    assert not any(f["edge_code"] == sea["code"] for f in detail["per_edge_flow"])
    assert any(e["code"] == sea["code"] for e in client.get("/api/countries/1/edges").json())
