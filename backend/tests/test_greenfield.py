"""Greenfield: where new stores would go, from the demand itself, and adopting the
proposal as a scenario the solver still has to justify."""

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


def test_proposals_sit_on_real_facilities_and_shorten_the_freight_task(client: TestClient):
    response = client.post("/api/countries/1/greenfield", json={"k": 2, "keep_existing": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["k"] == 2 and 1 <= len(body["proposals"]) <= 2
    nodes = {n["code"]: n for n in client.get("/api/countries/1/nodes").json()}
    for p in body["proposals"]:
        assert p["host_code"] in nodes and p["lat"] == nodes[p["host_code"]]["lat"], "snapped to a real facility"
        assert p["facility_count"] >= 1 and p["demand_m3"] > 0
        assert p["mean_km_after"] <= p["mean_km_before"]
        assert p["hub_fixed_cost"] > 0 and p["hub_throughput_m3"] >= p["demand_m3"]
    assert body["reduction"] is not None and body["reduction"] > 0
    assert "would serve" in body["sentence"] and "today's stores kept" in body["sentence"]
    # Deterministic: the same question gets the same answer.
    again = client.post("/api/countries/1/greenfield", json={"k": 2, "keep_existing": True}).json()
    assert [p["host_code"] for p in again["proposals"]] == [p["host_code"] for p in body["proposals"]]


def test_a_clean_map_and_a_province_filter(client: TestClient):
    clean = client.post("/api/countries/1/greenfield", json={"k": 3, "keep_existing": False}).json()
    assert len(clean["proposals"]) == 3 and clean["facilities_kept_by_existing_stores"] == 0
    assert "clean map" in clean["sentence"]
    province = client.post("/api/countries/1/greenfield", json={"k": 1, "admin1": "Morobe"}).json()
    assert all(p["admin1"] == "Morobe" for p in province["proposals"])
    assert client.post("/api/countries/1/greenfield", json={"k": 1, "admin1": "Atlantis"}).status_code == 400


def test_adopting_makes_a_scenario_of_items_the_solver_can_choose(client: TestClient):
    proposal = client.post("/api/countries/1/greenfield", json={"k": 2}).json()
    study = client.post("/api/countries/1/studies", json={"question": "Where next?"}, headers=AUTHOR).json()
    adopted = client.post(
        "/api/countries/1/greenfield/adopt",
        json={"proposals": proposal["proposals"], "keep_existing": True, "study_id": study["id"]},
        headers=AUTHOR,
    )
    assert adopted.status_code == 201, adopted.text
    scenario = adopted.json()["scenario"]
    assert scenario["name"].startswith("Greenfield: 2 new stores near")
    assert scenario["levers"]["optimize_hubs"] is True
    kinds = [i["kind"] for i in scenario["data_items"]]
    assert kinds.count("add_node") == 2
    assert kinds.count("add_lane") == 2 + sum(p["facility_count"] for p in proposal["proposals"])
    assert client.get(f"/api/studies/{study['id']}").json()["scenario_ids"][-1] == scenario["id"]

    result = client.post(f"/api/scenarios/{scenario['id']}/run").json()
    assert result["status"] == "ok"
    detail = client.get(f"/api/results/{result['id']}").json()
    # The candidate stores exist in the run and nowhere else.
    codes = {n["code"] for n in client.get("/api/countries/1/nodes").json()}
    assert not any(code in codes for code in adopted.json()["store_codes"])
    assert len(detail["solver_log"]["hubs_open_codes"]) >= 5, "today's stores stayed open"
    compare = client.get(f"/api/studies/{study['id']}/compare").json()
    texts = [d["text"] for d in compare["lever_diff"][str(scenario["id"])]["differences"]]
    assert sum(1 for t in texts if t.startswith("data: add store")) == 2
