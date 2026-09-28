"""Studies: a question with its ordered scenarios, presets that make the classic ones,
a compare view that says what differs and what it did, and a diff map."""

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


def test_presets_are_listed():
    from app.studies import PRESETS, presets_out

    keys = {p["key"] for p in presets_out()}
    assert keys == set(PRESETS) and {"cost", "modes", "wet_season"} <= keys


def test_a_study_starts_with_the_baseline_and_a_preset_adds_its_scenarios(client: TestClient):
    response = client.post(
        "/api/countries/1/studies",
        json={"question": "Land, air or sea?", "preset": "modes"},
        headers=AUTHOR,
    )
    assert response.status_code == 201, response.text
    study = response.json()
    assert study["question"] == "Land, air or sea?"
    names = [s["name"] for s in study["scenarios"]]
    assert study["scenarios"][0]["is_baseline"] is True
    assert names[1:] == ["Road only", "Surface only (road, sea, river)", "All modes including air"]
    # The preset scenarios are ordinary scenarios, cloned from the baseline, tagged for the study.
    scenarios = {s["name"]: s for s in client.get("/api/countries/1/scenarios").json()}
    assert scenarios["Road only"]["levers"]["allowed_modes"] == ["road"]
    assert "study" in scenarios["Road only"]["tags"] and "modes" in scenarios["Road only"]["tags"]
    assert scenarios["Road only"]["parent_scenario_id"] == study["scenarios"][0]["id"]

    # Ledger: the scenarios and the study were recorded with the author's claim.
    audit = client.get("/api/countries/1/audit").json()
    assert any(row.get("entity_type") == "study" and row.get("field") == "created" for row in audit)
    made = [row for row in audit if row.get("entity_type") == "scenario" and "preset" in (row.get("new_value") or "")]
    assert len(made) == 3 and made[0]["author_claim"] == "Ulrich, JSI"


def test_the_wet_season_preset_picks_the_wettest_month(client: TestClient):
    study = client.post("/api/countries/1/studies", json={"question": "What does the wet season do?", "preset": "wet_season"}, headers=AUTHOR).json()
    name = study["scenarios"][1]["name"]
    assert name.startswith("Wet season stress (")
    scenario = next(s for s in client.get("/api/countries/1/scenarios").json() if s["name"] == name)
    assert 1 <= scenario["levers"]["month"] <= 12


def test_running_and_comparing_a_study(client: TestClient):
    study = client.post("/api/countries/1/studies", json={"question": "Can we spend less?", "preset": "cost"}, headers=AUTHOR).json()
    ran = client.post(f"/api/studies/{study['id']}/run").json()
    assert ran["ran"] == 2 and all(r["status"] == "ok" for r in ran["results"])

    compare = client.get(f"/api/studies/{study['id']}/compare").json()
    rows = compare["rows"]
    assert [r["is_baseline"] for r in rows] == [True, False]
    option = rows[1]
    assert option["comparison"]["total_cost"]["direction"] == "better"
    assert option["confidence"]["tested"] is True

    diff = compare["lever_diff"][str(option["scenario_id"])]
    labels = {d["label"] for d in diff["differences"]}
    assert {"stores free to open or close", "service weight", "equity weight"} <= labels
    assert "differ" in diff["sentence"]

    equity = compare["equity"]
    assert len(equity) == 2 and len(equity[0]["strata"]) == 5

    moved = compare["facilities"][str(option["scenario_id"])]
    assert moved["lost_count"] >= 1 and moved["lost"][0]["name"]
    assert "cheapest" in compare["verdict"] and "No option is backed yet" in compare["verdict"]


def test_recommending_an_option_puts_it_in_the_verdict(client: TestClient):
    study = client.post("/api/countries/1/studies", json={"question": "Which one?", "preset": "cost"}, headers=AUTHOR).json()
    client.post(f"/api/studies/{study['id']}/run")
    option_id = study["scenarios"][1]["id"]
    stranger = client.post("/api/scenarios/1/clone", json={"name": "Not in the study"}, headers=AUTHOR).json()["id"]
    refused = client.patch(f"/api/studies/{study['id']}", json={"recommended_scenario_id": stranger}, headers=AUTHOR)
    assert refused.status_code == 400
    updated = client.patch(f"/api/studies/{study['id']}", json={"recommended_scenario_id": option_id}, headers=AUTHOR).json()
    assert updated["recommended_scenario_id"] == option_id
    verdict = client.get(f"/api/studies/{study['id']}/compare").json()["verdict"]
    assert "The analyst backs “Cost-optimised" in verdict
    audit = client.get("/api/countries/1/audit").json()
    assert any(row.get("entity_type") == "study" and row.get("field") == "recommended" for row in audit)


def test_scenarios_can_be_added_removed_and_reordered_but_the_baseline_stays_first(client: TestClient):
    study = client.post("/api/countries/1/studies", json={"question": "Order"}, headers=AUTHOR).json()
    assert len(study["scenario_ids"]) == 1
    added = client.post(f"/api/studies/{study['id']}/scenarios", json={"scenario_id": 2}).json()
    added = client.post(f"/api/studies/{study['id']}/scenarios", json={"scenario_id": 3}).json()
    assert added["scenario_ids"] == [1, 2, 3]
    reordered = client.patch(f"/api/studies/{study['id']}", json={"scenario_ids": [3, 2, 1]}, headers=AUTHOR).json()
    assert reordered["scenario_ids"] == [1, 3, 2], "the baseline is always first"
    assert client.delete(f"/api/studies/{study['id']}/scenarios/1").status_code == 400
    removed = client.delete(f"/api/studies/{study['id']}/scenarios/3").json()
    assert removed["scenario_ids"] == [1, 2]
    assert client.delete(f"/api/studies/{study['id']}").status_code == 204
    assert client.get(f"/api/studies/{study['id']}").status_code == 404
    # The scenarios were never the study's to delete.
    assert len(client.get("/api/countries/1/scenarios").json()) >= 7


def test_the_diff_map_names_what_only_one_network_uses(client: TestClient):
    study = client.post("/api/countries/1/studies", json={"question": "Roads?", "preset": "modes"}, headers=AUTHOR).json()
    client.post(f"/api/studies/{study['id']}/run")
    baseline_id, road_only = study["scenarios"][0]["id"], study["scenarios"][1]["id"]
    diff = client.get(f"/api/scenarios/{baseline_id}/diff-map/{road_only}").json()
    assert diff["a"]["scenario_name"].startswith("Baseline") and diff["b"]["scenario_name"].startswith("Road only")
    statuses = {lane["status"] for lane in diff["lanes"]}
    assert {"a", "both"} <= statuses
    assert all(lane["mode"] == "road" for lane in diff["lanes"] if lane["status"] in ("b", "both") and lane["volume_b"])
    assert diff["summary"]["lanes_only_a"] > 0
    # Facilities that lost their sea or air supply are named, with the hub they had.
    lost = [f for f in diff["facilities"] if f["change"] == "lost"]
    assert lost and lost[0]["name"] and lost[0]["hub_a_name"]
    assert client.get("/api/scenarios/1/diff-map/999").status_code == 409


def test_the_report_opens_on_a_decision_page(client: TestClient):
    client.post("/api/scenarios/1/run")
    client.post("/api/scenarios/2/run")
    page = client.get("/api/scenarios/2/report.html").text
    assert 'id="decision"' in page and page.index('id="decision"') < page.index('id="figures"')
    assert '<svg class="map"' in page and "Badili National Medical Store" in page
    assert "Stores under this plan" in page and "Facilities served" in page
    assert "people no longer supplied in full" in page or "people newly supplied" in page or "no change" in page
    assert "Who carries it." in page and "How sure this is." in page
    assert 'href="#equity"' in page and 'href="#confidence"' in page and 'href="#figures"' in page
    assert "Assumptions annex" in page and page.index("Assumptions annex") > page.index("What this rests on")
    # Still one self-contained file.
    for marker in ("<script", "https://", "http://"):
        assert marker not in page


def test_a_study_report_leads_with_the_question_and_the_backed_option(client: TestClient):
    study = client.post("/api/countries/1/studies", json={"question": "Can we spend less without abandoning anyone?", "preset": "cost"}, headers=AUTHOR).json()
    client.post(f"/api/studies/{study['id']}/run")
    unbacked = client.get(f"/api/studies/{study['id']}/report.html").text
    assert "Can we spend less without abandoning anyone?" in unbacked
    assert "The options considered" in unbacked and "No option is backed yet" in unbacked
    option_id = study["scenarios"][1]["id"]
    client.patch(f"/api/studies/{study['id']}", json={"recommended_scenario_id": option_id}, headers=AUTHOR)
    page = client.get(f"/api/studies/{study['id']}/report.html").text
    assert "<span class=tag>recommended</span>" in page
    assert "The analyst backs" in page
    assert "What each option changes" in page and "stores free to open or close" in page
    assert page.index('id="decision"') < page.index('id="options"') < page.index('id="figures"')
