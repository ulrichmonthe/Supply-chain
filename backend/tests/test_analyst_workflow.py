"""The analyst's flow, end to end: open a country, fix what is wrong, hand over a report.

Each of these covered a step that used to be impossible. Opening a second country meant
editing Python; correcting a flagged coordinate meant reopening the workbook; and the
only deliverable was a spreadsheet.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.db import get_session
from app.engine.distance import resolve_distance
from app.engine.geo import offshore_km
from app.io.schema_spec import SHEETS
from app.main import app
from app.seed.png import BOUNDARY as PNG_BOUNDARY


@pytest.fixture
def seeded_session_factory(isolated_session_factory):
    """These tests create countries and commit imports, so they get their own database."""
    return isolated_session_factory


@pytest.fixture
def client(seeded_session_factory):
    def _session_override():
        session = seeded_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _session_override
    # The app's own lifespan seeds a different database; the override is what the
    # endpoints actually use, so the client is constructed without running it.
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- opening a country ---------------------------------------------------------------


def test_a_second_country_needs_no_code_change(client: TestClient):
    created = client.post(
        "/api/countries",
        json={"code": "slb", "name": "Solomon Islands", "currency": "SBD"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["code"] == "SLB", "the code is normalised so PNG and png cannot both exist"
    assert body["currency"] == "SBD"
    assert body["has_boundary"] is False, "a new country has no land mask until somebody draws one"


def test_a_country_code_cannot_be_taken_twice(client: TestClient):
    client.post("/api/countries", json={"code": "NPL", "name": "Nepal"})
    again = client.post("/api/countries", json={"code": "NPL", "name": "Nepal again"})
    assert again.status_code == 409
    assert "already exists" in again.json()["detail"]


def test_a_new_country_gets_a_bounding_box_that_rejects_nothing(client: TestClient):
    """An invented bounding box would reject real facilities, which is worse than no check."""
    body = client.post("/api/countries", json={"code": "MDG", "name": "Madagascar"}).json()
    bbox = body["config"]["bbox"]
    assert (bbox["min_lat"], bbox["max_lat"]) == (-90, 90)
    assert (bbox["min_lon"], bbox["max_lon"]) == (-180, 180)


def test_changing_the_boundary_is_recorded(client: TestClient):
    country = client.post("/api/countries", json={"code": "VUT", "name": "Vanuatu"}).json()
    patched = client.patch(
        f"/api/countries/{country['id']}",
        json={"boundary": {"polygons": [], "buffers": [[-17.74, 168.31, 30]]}},
    )
    assert patched.status_code == 200
    assert patched.json()["has_boundary"] is True

    trail = client.get(f"/api/countries/{country['id']}/audit").json()
    assert any(row["field"] == "boundary" for row in trail), "a changed land mask must be auditable"


# --- the geography is data, not code -------------------------------------------------


def test_each_country_is_screened_against_its_own_land_mask():
    """The check used to be a lookup on the country code, so only PNG had one."""
    solomons = {"polygons": [], "buffers": [[-9.43, 159.96, 40.0]]}

    assert offshore_km(solomons, -9.43, 159.96) == 0.0, "Honiara is on land"
    assert offshore_km(solomons, -9.43, 150.0) > 500, "the open Pacific is not"

    # PNG's own mask is untouched by the existence of another country.
    assert offshore_km(PNG_BOUNDARY, -9.4747, 147.1925) == 0.0
    assert offshore_km(PNG_BOUNDARY, -3.5, 149.0) > 100


def test_a_country_without_a_boundary_loses_the_check_rather_than_failing_everything():
    for empty in (None, {}, {"polygons": [], "buffers": []}):
        assert offshore_km(empty, -1.29, 36.82) is None


def test_terrain_assumptions_can_differ_between_countries():
    """A highland road in Nepal is not a highland road in Papua New Guinea."""
    default = resolve_distance(-9.44, 147.18, -6.73, 146.98, mode="road", terrain_class="highlands_road")
    tuned = resolve_distance(
        -9.44,
        147.18,
        -6.73,
        146.98,
        mode="road",
        terrain_class="highlands_road",
        config={"detour_factors": {"highlands_road": 2.4}},
    )
    assert tuned.distance_km > default.distance_km
    assert tuned.method == "detour_factor"
    assert "2.40 detour factor" in tuned.note


# --- fixing a flagged row without leaving the tool -----------------------------------


def _workbook(lat: float, lon: float) -> bytes:
    rows = {
        "Nodes": [
            ["HUB-1", "Central store", 0, "national_store", -9.4747, 147.1925, "mfl", 0.9,
             "Central", "Port Moresby", "mainland_road", 120000, "operational",
             900, 220, 40, 0, "TRUE", 400000, 0, 9000],
            ["FAC-SEA", "Offshore clinic", 3, "health_centre", lat, lon, "estimated", 0.3,
             "West New Britain", "Talasea", "island", 24000, "operational",
             30, 6, 0, 0, "FALSE", 0, 0, 0],
        ],
        "Products": [["KIT", "Essential medicines kit", "ambient", 62000.0, 180.0, 720]],
        "Demand": [["FAC-SEA", "KIT", 0, 900, "forecast", 0.6]],
        "Edges": [
            ["L1", "HUB-1", "FAC-SEA", "sea", "Coastal run", "FORTNIGHTLY", "TUE",
             60, 8, 9000, 0, 0, "", "", "", "", "", "island_sea"] + [1] * 12 + [0.9, 1.5, "TRUE"]
        ],
    }
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, columns in SHEETS.items():
        sheet = workbook.create_sheet(name)
        sheet.append([column for column, _ in columns])
        for row in rows.get(name, []):
            sheet.append(list(row) + [None] * (len(columns) - len(row)))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def offshore_batch(client: TestClient):
    """An uploaded workbook whose clinic sits in the Bismarck Sea."""
    upload = client.post(
        "/api/countries/1/validate",
        files={"file": ("network.xlsx", _workbook(-3.5, 149.0),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload.status_code == 200
    report = upload.json()
    assert report["blocking"] is True
    assert "node.offshore" in {issue["code"] for issue in report["issues"]}
    return report


def test_a_flagged_row_can_be_corrected_in_place(client: TestClient, offshore_batch):
    fixed = client.patch(
        f"/api/imports/{offshore_batch['batch_id']}/rows",
        json={
            "sheet": "nodes",
            "key": "FAC-SEA",
            "values": {"lat": -5.5510, "lon": 150.1430, "geocode_source": "gps", "geocode_confidence": 0.95},
            "reason": "Provincial officer gave the GPS fix.",
        },
    )
    assert fixed.status_code == 200
    body = fixed.json()
    assert body["blocking"] is False, "correcting the coordinate should clear the block"
    assert "node.offshore" not in {issue["code"] for issue in body["issues"]}


def test_a_correction_records_what_it_replaced_and_why(client: TestClient, offshore_batch):
    body = client.patch(
        f"/api/imports/{offshore_batch['batch_id']}/rows",
        json={
            "sheet": "nodes",
            "key": "FAC-SEA",
            "values": {"lat": -5.5510, "lon": 150.1430},
            "reason": "Provincial officer gave the GPS fix.",
        },
    ).json()
    correction = body["corrections"][0]
    assert correction["before"]["lat"] == -3.5
    assert correction["after"]["lat"] == -5.551
    assert "Provincial officer" in correction["reason"]


def test_correcting_a_row_writes_nothing_to_the_model(client: TestClient, offshore_batch):
    """The two-step import is the whole safety property; a correction must not bypass it."""
    before = {node["code"] for node in client.get("/api/countries/1/nodes").json()}
    client.patch(
        f"/api/imports/{offshore_batch['batch_id']}/rows",
        json={"sheet": "nodes", "key": "FAC-SEA", "values": {"lat": -5.551, "lon": 150.143}},
    )
    after = {node["code"] for node in client.get("/api/countries/1/nodes").json()}
    assert before == after
    assert "FAC-SEA" not in after


def test_an_unknown_row_says_so_rather_than_silently_doing_nothing(client: TestClient, offshore_batch):
    missing = client.patch(
        f"/api/imports/{offshore_batch['batch_id']}/rows",
        json={"sheet": "nodes", "key": "NOT-A-FACILITY", "values": {"lat": 0.0}},
    )
    assert missing.status_code == 404
    assert "NOT-A-FACILITY" in missing.json()["detail"]


def test_a_committed_import_cannot_be_corrected(client: TestClient, offshore_batch):
    batch_id = offshore_batch["batch_id"]
    client.patch(
        f"/api/imports/{batch_id}/rows",
        json={"sheet": "nodes", "key": "FAC-SEA", "values": {"lat": -5.551, "lon": 150.143}},
    )
    assert client.post(f"/api/imports/{batch_id}/commit?replace=false").status_code == 200

    late = client.patch(
        f"/api/imports/{batch_id}/rows",
        json={"sheet": "nodes", "key": "FAC-SEA", "values": {"lat": 0.0}},
    )
    assert late.status_code == 409


# --- the deliverable -----------------------------------------------------------------


def test_the_report_names_the_trade_off_rather_than_reporting_a_percentage(client: TestClient):
    client.post("/api/scenarios/1/run")
    client.post("/api/scenarios/2/run")
    response = client.get("/api/scenarios/2/report.html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    page = response.text
    assert "most vulnerable fifth of the population" in page
    assert "move from supplied to not supplied" in page, "the people, not just the percentage"
    assert "Who carries this plan" in page


def test_the_report_is_self_contained(client: TestClient):
    """It has to survive being emailed and open on a machine with no internet."""
    client.post("/api/scenarios/1/run")
    page = client.get("/api/scenarios/1/report.html").text
    for marker in ("<script", "src=", "@import", "https://", "http://"):
        assert marker not in page, f"the report must not reach for {marker}"


def test_the_report_states_what_it_rests_on(client: TestClient):
    client.post("/api/scenarios/1/run")
    page = client.get("/api/scenarios/1/report.html").text
    assert "What this rests on" in page
    assert "estimated distance rather than a measured" in page
    assert "not from records of routes actually closing" in page


def test_a_scenario_with_no_run_says_so_instead_of_producing_an_empty_report(client: TestClient):
    created = client.post(
        "/api/countries/1/scenarios",
        json={"name": "Never run", "description": "", "levers": {}, "constraints": {}},
    ).json()
    response = client.get(f"/api/scenarios/{created['id']}/report.html")
    assert response.status_code == 409
    assert "Run this scenario" in response.json()["detail"]
