"""The ledger: who claims a change, in which sitting, and how it reads back.

There are no accounts yet. What there is instead is an author claim -- the name a
person types once and the interface sends with every write -- and these tests are
what stop that claim from quietly becoming optional, unbounded, or lost on one of the
paths that writes to the model.
"""

from __future__ import annotations

import io
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.api.deps import ANONYMOUS, clean_claim
from app.db import get_session
from app.io.schema_spec import SHEETS
from app.main import app

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


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


def _latest(client: TestClient, **params):
    return client.get("/api/countries/1/audit", params={"limit": 1, **params}).json()[0]


# --- the claim itself ------------------------------------------------------------------


def test_a_claim_is_tidied_but_kept_as_typed():
    assert clean_claim("  Ulrich   Monthe, JSI ") == "Ulrich Monthe, JSI"


def test_no_claim_reads_as_anonymous_not_blank():
    for empty in (None, "", "   ", "\t\n"):
        assert clean_claim(empty) == ANONYMOUS


def test_a_claim_cannot_be_longer_than_the_column():
    assert len(clean_claim("x" * 500)) == 96


# --- every path that writes to the model signs its work --------------------------------


def test_a_lever_change_is_claimed_by_whoever_sent_it(client: TestClient):
    client.patch(
        "/api/scenarios/2",
        json={"objective_weights": {"cost": 1.0, "service": 0.25, "equity": 0.4}},
        headers={"X-Author": "Ulrich, JSI"},
    )
    entry = _latest(client, entity_type="scenario")
    assert entry["author_claim"] == "Ulrich, JSI"
    assert entry["field"] == "objective_weights.equity"
    assert entry["old_value"] == "0"
    assert entry["new_value"] == "0.4"
    assert UUID.match(entry["batch_id"])
    assert entry["status"] == "applied"


def test_a_write_without_a_claim_is_recorded_as_anonymous(client: TestClient):
    client.patch("/api/scenarios/3", json={"description": "Reworded without signing."})
    entry = _latest(client, entity_type="scenario", entity_ref="Cost optimisation with a 90% equity floor")
    assert entry["author_claim"] == ANONYMOUS
    assert entry["field"] == "description"


def test_a_country_setting_change_is_claimed(client: TestClient):
    client.patch("/api/countries/1", json={"currency": "USD"}, headers={"X-Author": "Steward"})
    entry = _latest(client, entity_type="global", entity_ref="PNG")
    assert (entry["field"], entry["author_claim"]) == ("currency", "Steward")
    client.patch("/api/countries/1", json={"currency": "PGK"})


def test_a_lane_override_is_claimed(client: TestClient):
    edge = client.get("/api/countries/1/edges").json()[0]
    client.patch(
        f"/api/edges/{edge['id']}",
        json={"reliability": 0.5, "rationale": "Field interview."},
        headers={"X-Author": "Provincial logistician"},
    )
    entry = _latest(client, entity_type="edge", entity_ref=edge["code"])
    assert entry["author_claim"] == "Provincial logistician"
    assert entry["provenance"] == "manual_override"


def _workbook() -> bytes:
    rows = {
        "Nodes": [
            ["HUB-1", "Central store", 0, "national_store", -9.4747, 147.1925, "mfl", 0.9,
             "Central", "Port Moresby", "mainland_road", 120000, "operational",
             900, 220, 40, 0, "TRUE", 400000, 0, 9000],
            ["FAC-1", "Kimbe clinic", 3, "health_centre", -5.5510, 150.1430, "gps", 0.95,
             "West New Britain", "Talasea", "island", 24000, "operational",
             30, 6, 0, 0, "FALSE", 0, 0, 0],
        ],
        "Products": [["KIT", "Essential medicines kit", "ambient", 62000.0, 180.0, 720]],
        "Demand": [["FAC-1", "KIT", 0, 900, "forecast", 0.6]],
        "Edges": [
            ["L1", "HUB-1", "FAC-1", "sea", "Coastal run", "FORTNIGHTLY", "TUE",
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


def test_an_import_is_claimed_and_carries_one_batch_id(client: TestClient):
    upload = client.post(
        "/api/countries/1/validate",
        files={"file": ("net.xlsx", _workbook(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    assert upload["blocking"] is False, upload["issues"]
    client.post(f"/api/imports/{upload['batch_id']}/commit?replace=false", headers={"X-Author": "Data steward"})

    entry = _latest(client, entity_type="global")
    assert entry["author_claim"] == "Data steward"
    assert entry["provenance"] == "import"
    assert UUID.match(entry["batch_id"])
    assert client.get("/api/countries/1/audit", params={"batch_id": entry["batch_id"]}).json() == [entry]


def test_a_correction_inside_an_import_names_who_made_it(client: TestClient):
    upload = client.post(
        "/api/countries/1/validate",
        files={"file": ("net.xlsx", _workbook(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    fixed = client.patch(
        f"/api/imports/{upload['batch_id']}/rows",
        json={"sheet": "nodes", "key": "FAC-1", "values": {"catchment_population": 25000}},
        headers={"X-Author": "Reviewer"},
    ).json()
    assert fixed["corrections"][-1]["author"] == "Reviewer"


# --- a sitting is one row, not forty ---------------------------------------------------


def test_slider_moves_in_one_sitting_collapse_to_one_row(client: TestClient):
    before = len(client.get("/api/countries/1/audit", params={"entity_type": "scenario", "limit": 1000}).json())
    for value in (0.1, 0.2, 0.3, 0.45, 0.6):
        client.patch(
            "/api/scenarios/5",
            json={"objective_weights": {"cost": 1.0, "service": 1.0, "equity": value}},
            headers={"X-Author": "Analyst"},
        )
    after = client.get("/api/countries/1/audit", params={"entity_type": "scenario", "limit": 1000}).json()
    assert len(after) == before + 1, "five notches on one slider are one change"
    entry = after[0]
    assert (entry["old_value"], entry["new_value"]) == ("0.5", "0.6"), "first old value, last new value"


def test_sliding_back_to_where_you_started_leaves_no_row(client: TestClient):
    before = len(client.get("/api/countries/1/audit", params={"entity_type": "scenario", "limit": 1000}).json())
    client.patch(
        "/api/scenarios/6",
        json={"objective_weights": {"cost": 1.0, "service": 1.0, "equity": 0.9}},
        headers={"X-Author": "Analyst"},
    )
    client.patch(
        "/api/scenarios/6",
        json={"objective_weights": {"cost": 1.0, "service": 1.0, "equity": 0.5}},
        headers={"X-Author": "Analyst"},
    )
    after = len(client.get("/api/countries/1/audit", params={"entity_type": "scenario", "limit": 1000}).json())
    assert after == before, "0.5 -> 0.9 -> 0.5 is nothing, and the ledger says nothing"


def test_two_people_moving_the_same_lever_are_two_rows(client: TestClient):
    for who, value in (("First", 0.7), ("Second", 0.8)):
        client.patch(
            "/api/scenarios/7",
            json={"objective_weights": {"cost": 1.0, "service": 1.0, "equity": value}},
            headers={"X-Author": who},
        )
    rows = client.get("/api/countries/1/audit", params={"entity_ref": "Integrated programme supply chain"}).json()
    assert [row["author_claim"] for row in rows[:2]] == ["Second", "First"]


# --- reading it back -------------------------------------------------------------------


def test_the_ledger_can_be_filtered_by_author_ignoring_case(client: TestClient):
    client.patch("/api/scenarios/4", json={"description": "Signed."}, headers={"X-Author": "Nadia Rahman"})
    rows = client.get("/api/countries/1/audit", params={"author": "nadia rahman"}).json()
    assert rows and all(row["author_claim"] == "Nadia Rahman" for row in rows)


def test_the_ledger_pages_backwards_with_a_cursor(client: TestClient):
    first = client.get("/api/countries/1/audit", params={"limit": 2}).json()
    older = client.get("/api/countries/1/audit", params={"limit": 2, "before": first[-1]["id"]}).json()
    assert older and all(row["id"] < first[-1]["id"] for row in older)


def test_deleting_a_scenario_is_remembered_after_the_scenario_is_gone(client: TestClient):
    created = client.post(
        "/api/countries/1/scenarios", json={"name": "Short-lived"}, headers={"X-Author": "Analyst"}
    ).json()
    client.delete(f"/api/scenarios/{created['id']}", headers={"X-Author": "Analyst"})
    rows = client.get("/api/countries/1/audit", params={"entity_ref": "Short-lived"}).json()
    assert [row["field"] for row in rows] == ["deleted", "created"]


def test_a_duplicate_records_where_it_came_from(client: TestClient):
    clone = client.post("/api/scenarios/2/clone", json={"name": "Variant"}, headers={"X-Author": "Analyst"}).json()
    entry = _latest(client, entity_ref=clone["name"])
    assert "Duplicated from" in entry["new_value"]
