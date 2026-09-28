"""Estimates that show their working and stay live.

The blank the solver reads as zero, and the typed guess that reads as a fact, are the
two ways demand data goes wrong in a data-poor country. An estimate is the third way:
a named rule, its arithmetic beside the number, and the number tied to its inputs so
it moves when they do -- until a person types over it.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.db import get_session
from app.io.schema_spec import SHEETS
from app.main import app

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


def demand(client: TestClient, node_id: int):
    return {row["sku"]: row for row in client.get(f"/api/nodes/{node_id}/demand").json()}


def latest(client: TestClient, **params):
    return client.get("/api/countries/1/audit", params={"limit": 1, **params}).json()[0]


@pytest.fixture(scope="module")
def new_facility(isolated_session_factory):
    """A facility with no demand at all -- every product is a blank row."""
    def _session_override():
        session = isolated_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _session_override
    with TestClient(app) as test_client:
        created = test_client.post(
            "/api/countries/1/nodes",
            json={"code": "EST-1", "name": "Estimated clinic", "lat": -6.1, "lon": 145.4, "catchment_population": 12000, "type": "health_centre", "admin1": "Eastern Highlands"},
            headers={"X-Author": "Analyst"},
        )
        assert created.status_code == 201, created.text
    app.dependency_overrides.clear()
    return created.json()["node"]


# --- the seeded workspace already says what it is -------------------------------------


def test_seeded_demand_is_recorded_as_the_estimate_it_is(client: TestClient):
    facility = next(n for n in nodes(client) if n["level"] == 3)
    rows = demand(client, facility["id"])
    assert rows, "seeded facilities carry demand"
    for row in rows.values():
        assert row["derivation"]["rule"] == "population_rate"
        assert "per 1,000" in row["derivation"]["formula"]
    assert "capacity" in facility["derivations"]


def test_the_overview_states_how_much_demand_is_estimated(client: TestClient):
    estimated = client.get("/api/countries/1/overview").json()["estimated"]
    assert estimated["demand_rows"] > 1000
    assert estimated["demand_share"] == 1.0, "every seeded row is a population proxy, and the tool says so"


def test_the_rules_describe_what_they_would_use(client: TestClient):
    described = client.get("/api/countries/1/estimators").json()
    assert {r["key"] for r in described["rules"]} == {"population_rate", "peer_median", "capacity_cover"}
    kit = next(p for p in described["products"] if p["sku"] == "ESSMED-KIT")
    assert kit["population_rate"]["available"] is True
    assert kit["population_rate"]["per_1000"] > 0
    assert "country's settings" in kit["population_rate"]["basis"]
    assert kit["estimated"] == kit["rows"]


# --- filling blanks ----------------------------------------------------------------------


def test_a_new_facility_has_only_blank_rows(client: TestClient, new_facility):
    kit = next(p for p in client.get("/api/countries/1/estimators").json()["products"] if p["sku"] == "ESSMED-KIT")
    assert kit["blank"] == 1
    assert demand(client, new_facility["id"]) == {}


def test_preview_shows_the_arithmetic_and_writes_nothing(client: TestClient, new_facility):
    preview = client.post("/api/countries/1/estimates/preview", json={"rule": "population_rate", "sku": "ESSMED-KIT"}).json()
    assert preview["count"] == 1
    proposal = preview["proposals"][0]
    assert proposal["node_code"] == "EST-1"
    assert proposal["current"] is None
    assert "12,000 people ×" in proposal["formula"]
    assert demand(client, new_facility["id"]) == {}, "a preview writes nothing"


def test_applying_fills_the_blank_with_a_live_estimate_and_a_ledger_row(client: TestClient, new_facility):
    outcome = client.post(
        "/api/countries/1/estimates/apply",
        json={"rule": "population_rate", "sku": "ESSMED-KIT", "reason": "No consumption data for the new site."},
        headers={"X-Author": "Analyst"},
    ).json()
    assert outcome["applied"] == 1

    row = demand(client, new_facility["id"])["ESSMED-KIT"]
    assert row["quantity"] > 0
    assert row["source"] == "proxy"
    assert row["derivation"]["rule"] == "population_rate"
    assert row["derivation"]["inputs"]["population"] == 12000

    entry = latest(client, entity_ref="EST-1/ESSMED-KIT/0")
    assert entry["provenance"] == "derived"
    assert entry["author_claim"] == "Analyst"
    assert "12,000 people ×" in entry["rationale"]
    assert "No consumption data" in entry["rationale"]


def test_filling_blanks_never_touches_a_row_somebody_filled(client: TestClient, new_facility):
    before = demand(client, new_facility["id"])["ESSMED-KIT"]["quantity"]
    again = client.post("/api/countries/1/estimates/apply", json={"rule": "population_rate", "sku": "ESSMED-KIT"}).json()
    assert again["applied"] == 0, "the only blank row was filled last time"
    assert demand(client, new_facility["id"])["ESSMED-KIT"]["quantity"] == before


def test_one_named_row_can_be_estimated_even_when_it_is_not_blank(client: TestClient, new_facility):
    outcome = client.post(
        "/api/countries/1/estimates/apply",
        json={"rule": "peer_median", "sku": "ESSMED-KIT", "node_id": new_facility["id"]},
    ).json()
    assert outcome["applied"] == 1
    row = demand(client, new_facility["id"])["ESSMED-KIT"]
    assert row["derivation"]["rule"] == "peer_median"
    assert "health_centre" not in row["derivation"]["formula"] or "type-matched" in row["derivation"]["formula"]


# --- staying live --------------------------------------------------------------------------


def test_a_population_change_moves_the_estimate(client: TestClient, new_facility):
    client.post("/api/countries/1/estimates/apply", json={"rule": "population_rate", "sku": "MAL-KIT", "node_id": new_facility["id"]})
    before = demand(client, new_facility["id"])["MAL-KIT"]["quantity"]

    patched = client.patch(f"/api/nodes/{new_facility['id']}", json={"catchment_population": 24000}, headers={"X-Author": "Steward"})
    assert patched.status_code == 200
    assert patched.json()["recomputed"] >= 1

    after = demand(client, new_facility["id"])["MAL-KIT"]
    assert after["quantity"] == pytest.approx(before * 2, rel=1e-6)
    assert after["derivation"]["inputs"]["population"] == 24000
    entry = latest(client, entity_ref="EST-1/MAL-KIT/0")
    assert entry["rationale"].startswith("Recomputed")
    assert entry["provenance"] == "derived"


def test_typing_over_an_estimate_pins_it(client: TestClient, new_facility):
    client.put(
        f"/api/nodes/{new_facility['id']}/demand",
        json={"lines": [{"sku": "MAL-KIT", "quantity": 500, "source": "actual", "confidence": 0.9}], "confidence_marker": "S"},
        headers={"X-Author": "Steward"},
    )
    pinned = demand(client, new_facility["id"])["MAL-KIT"]
    assert pinned["derivation"] is None
    assert pinned["quantity"] == 500

    client.patch(f"/api/nodes/{new_facility['id']}", json={"catchment_population": 48000})
    assert demand(client, new_facility["id"])["MAL-KIT"]["quantity"] == 500, "a typed figure does not follow the population"
    assert demand(client, new_facility["id"])["ESSMED-KIT"]["derivation"] is not None, "the still-estimated row does"


def test_storage_is_sized_from_demand_and_follows_it(client: TestClient, new_facility):
    # By now the facility's KIT row is a peer-median estimate (no live input) and its
    # MAL-KIT row is pinned. Storage can only follow demand that itself follows
    # population, so give it one such row first -- an ambient product, so it is the
    # dry store that moves.
    client.post("/api/countries/1/estimates/apply", json={"rule": "population_rate", "sku": "MEDSUP-BOX", "node_id": new_facility["id"]})
    preview = client.post("/api/countries/1/estimates/preview", json={"rule": "capacity_cover", "node_id": new_facility["id"]}).json()
    assert preview["count"] == 1, preview
    assert "cover days" in preview["proposals"][0]["formula"]
    client.post("/api/countries/1/estimates/apply", json={"rule": "capacity_cover", "node_id": new_facility["id"]})
    node = next(n for n in nodes(client) if n["id"] == new_facility["id"])
    assert node["capacity"]["dry_m3"] > 0
    assert node["derivations"]["capacity"]["rule"] == "capacity_cover"
    dry_before = node["capacity"]["dry_m3"]

    client.patch(f"/api/nodes/{new_facility['id']}", json={"catchment_population": 96000})
    node = next(n for n in nodes(client) if n["id"] == new_facility["id"])
    assert node["capacity"]["dry_m3"] > dry_before, "more people, more estimated demand, more storage"


def test_typing_a_capacity_pins_it(client: TestClient, new_facility):
    client.patch(f"/api/nodes/{new_facility['id']}", json={"capacity": {"dry_m3": 3.5, "cold_by_band": {"+2-8": 0.2}}}, headers={"X-Author": "Steward"})
    node = next(n for n in nodes(client) if n["id"] == new_facility["id"])
    assert "capacity" not in node["derivations"]
    client.patch(f"/api/nodes/{new_facility['id']}", json={"catchment_population": 120000})
    node = next(n for n in nodes(client) if n["id"] == new_facility["id"])
    assert node["capacity"]["dry_m3"] == 3.5


# --- an import pins too -------------------------------------------------------------------


def _merge_workbook(client: TestClient, node: dict, quantity: float) -> bytes:
    """A workbook naming the facility, the product and one measured demand row -- the
    validator wants the sheets to agree with each other, so the row's references are
    carried alongside it."""
    product = next(p for p in client.get("/api/countries/1/products").json() if p["sku"] == "ESSMED-KIT")
    hub = next(n for n in client.get("/api/countries/1/nodes").json() if n["level"] == 1)
    as_row = lambda n: [n["code"], n["name"], n["level"], n["type"], n["lat"], n["lon"], n["geocode_source"],
                        n["geocode_confidence"], n["admin1"], n["admin2"], n["terrain_class"],
                        n["catchment_population"], n["operating_status"]]
    rows = {
        # The validator wants the sheets to agree with each other, and a facility with
        # demand and no lane to deliver it is an error -- rightly. So the row brings its
        # facility, its product, a hub and a lane along.
        "Nodes": [as_row(hub), as_row(node)],
        "Products": [[product["sku"], product["name"], product["temperature_band"], product["volume_per_unit_cm3"],
                      product["unit_cost"], product["shelf_life_days"]]],
        "Demand": [[node["code"], "ESSMED-KIT", 0, quantity, "actual", 0.9]],
        "Edges": [
            [f"L-{node['code']}", hub["code"], node["code"], "road", f"Road to {node['code']}", "MONTHLY", "MON",
             0, 0, 9000, 0, 0, "", "", "", "", "", "mainland_road"] + [1] * 12 + [0.9, 1.5, "TRUE"]
        ],
    }
    book = Workbook()
    book.remove(book.active)
    for name, columns in SHEETS.items():
        sheet = book.create_sheet(name)
        sheet.append([column for column, _ in columns])
        for row in rows.get(name, []):
            sheet.append(list(row) + [None] * (len(columns) - len(row)))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_a_file_that_supplies_the_figure_pins_the_estimate(client: TestClient, new_facility):
    assert demand(client, new_facility["id"])["ESSMED-KIT"]["derivation"] is not None
    current = next(n for n in nodes(client) if n["id"] == new_facility["id"])
    report = client.post("/api/countries/1/validate", files={"file": ("d.xlsx", _merge_workbook(client, current, 777), XLSX)}).json()
    assert report["blocking"] is False, report["issues"]
    client.post(f"/api/imports/{report['batch_id']}/commit?replace=false", headers={"X-Author": "Importer"})
    row = demand(client, new_facility["id"])["ESSMED-KIT"]
    assert row["quantity"] == 777
    assert row["derivation"] is None, "the file supplied a figure: it is data now, not an estimate"


# --- when a rule has nothing to work from -------------------------------------------------


def test_a_rule_says_why_it_cannot_estimate(client: TestClient):
    created = client.post("/api/countries", json={"code": "EMP", "name": "Empty"}).json()
    described = client.get(f"/api/countries/{created['id']}/estimators").json()
    assert described["products"] == []
    preview = client.post(f"/api/countries/{created['id']}/estimates/preview", json={"rule": "capacity_cover"}).json()
    assert preview["count"] == 0
