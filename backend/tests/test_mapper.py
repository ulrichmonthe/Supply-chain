"""The column mapper: any CSV, mapped once to our columns, through the same validate,
preview and apply pipeline as a workbook."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.io import mapper
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


def _upload(name: str, text: str):
    return {"file": (name, text.encode("utf-8"), "text/csv")}


def test_headers_from_other_systems_are_recognised():
    columns = ["Facility Code", "Facility Name", "Latitude", "Longitude", "Province", "District", "Population", "Status"]
    assert mapper.guess_sheet(columns) == "Nodes"
    guess = mapper.guess_mapping(columns, "Nodes")
    assert guess["code"] == "Facility Code" and guess["name"] == "Facility Name"
    assert guess["lat"] == "Latitude" and guess["lon"] == "Longitude"
    assert guess["admin1"] == "Province" and guess["admin2"] == "District"
    assert guess["catchment_population"] == "Population" and guess["operating_status"] == "Status"

    demand = ["orgunit", "item_code", "month", "qty"]
    assert mapper.guess_sheet(demand) == "Demand"
    guess = mapper.guess_mapping(demand, "Demand")
    assert guess == {**guess, "node": "orgunit", "product": "item_code", "period": "month", "quantity": "qty"}


def test_the_reader_copes_with_semicolons_a_bom_and_blank_lines():
    data = "﻿code;name;lat;lon\r\nA;Alpha;-6.1;145.2\r\n\r\nB;Beta;-5.9;145.3\r\n".encode("utf-8")
    columns, rows, delimiter = mapper.read_csv(data)
    assert columns == ["code", "name", "lat", "lon"] and delimiter == ";"
    assert [r["code"] for r in rows] == ["A", "B"] and rows[1]["_row"] == 4


def test_inspect_guesses_the_sheet_and_shows_a_sample(client: TestClient):
    text = "Facility Code,Facility Name,Latitude,Longitude,Province,Population\nPNG-X-1,Test clinic,-6.1,145.4,Eastern Highlands,12000\n"
    response = client.post("/api/countries/1/imports/csv/inspect", files=_upload("mfl.csv", text))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sheet"] == "Nodes" and body["row_count"] == 1
    assert body["guesses"]["Nodes"]["code"] == "Facility Code"
    assert body["sample"][0]["Facility Name"] == "Test clinic"
    assert any(f["key"] == "lat" and f["required"] for f in body["fields"]["Nodes"])
    assert body["matched_saved"] is None and body["saved"] == []


def test_a_facility_csv_is_validated_partially_previewed_and_applied(client: TestClient):
    nodes = client.get("/api/countries/1/nodes").json()
    existing = next(n for n in nodes if n["level"] == 3)
    text = (
        "Facility Code,Facility Name,Latitude,Longitude,Province,Population\n"
        f"{existing['code']},{existing['name']},{existing['lat']},{existing['lon']},{existing['admin1']},{int(existing['catchment_population']) + 1000}\n"
        "PNG-CSV-1,Mapped clinic,-6.05,145.45,Eastern Highlands,9000\n"
    )
    mapping = {"code": "Facility Code", "name": "Facility Name", "lat": "Latitude", "lon": "Longitude", "admin1": "Province", "catchment_population": "Population"}
    response = client.post(
        "/api/countries/1/imports/csv",
        files=_upload("mfl.csv", text),
        data={"sheet": "Nodes", "mapping": __import__("json").dumps(mapping), "save_as": "Ministry facility list"},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    # No lanes in a facility list is not an error: the validation ran in partial mode.
    assert report["blocking"] is False, report["issues"][:3]
    assert report["source"] == "csv" and report["sheet"] == "Nodes" and report["rows"] == 2
    assert report["saved"][0]["name"] == "Ministry facility list"

    changes = client.get(f"/api/imports/{report['batch_id']}/changes", params={"replace": False}).json()
    assert changes["nodes"]["counts"]["add"] == 1 and changes["nodes"]["counts"]["update"] >= 1
    assert changes["nodes"]["counts"]["retire"] == 0, "a merge never retires what the file does not mention"

    applied = client.post(f"/api/imports/{report['batch_id']}/commit", params={"replace": False}, headers=AUTHOR).json()
    assert applied["counts"]["nodes_created"] == 1 and applied["counts"]["nodes_updated"] >= 1
    after = {n["code"]: n for n in client.get("/api/countries/1/nodes").json()}
    assert after["PNG-CSV-1"]["name"] == "Mapped clinic"
    assert after[existing["code"]]["catchment_population"] == existing["catchment_population"] + 1000
    assert len(after) == len(nodes) + 1

    # The mapping is kept for next time and recognised on the next file with those columns.
    again = client.post("/api/countries/1/imports/csv/inspect", files=_upload("mfl2.csv", text)).json()
    assert again["matched_saved"] == "Ministry facility list" and again["sheet"] == "Nodes"
    assert client.get("/api/countries/1/column-mappings").json()[0]["mapping"]["lat"] == "Latitude"
    assert client.delete("/api/countries/1/column-mappings/Ministry facility list").status_code == 204
    assert client.get("/api/countries/1/column-mappings").json() == []


def test_a_consumption_extract_pins_the_estimates_it_replaces(client: TestClient):
    nodes = [n for n in client.get("/api/countries/1/nodes").json() if n["level"] == 3]
    node = nodes[3]
    before = client.get(f"/api/nodes/{node['id']}/demand").json()
    row = next(r for r in before if r["derivation"])
    text = f"orgunit,item_code,qty\n{node['code']},{row['sku']},{int(row['quantity']) + 77}\n"
    response = client.post(
        "/api/countries/1/imports/csv",
        files=_upload("consumption.csv", text),
        data={"sheet": "Demand", "mapping": '{"node": "orgunit", "product": "item_code", "quantity": "qty"}'},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["blocking"] is False, report["issues"][:3]
    client.post(f"/api/imports/{report['batch_id']}/commit", params={"replace": False}, headers=AUTHOR)
    after = next(r for r in client.get(f"/api/nodes/{node['id']}/demand").json() if r["sku"] == row["sku"])
    assert after["quantity"] == int(row["quantity"]) + 77
    assert after["derivation"] is None, "a figure the file supplied pins the row"


def test_missing_required_columns_and_workbooks_are_refused(client: TestClient):
    response = client.post(
        "/api/countries/1/imports/csv",
        files=_upload("half.csv", "Facility Code,Facility Name\nA,B\n"),
        data={"sheet": "Nodes", "mapping": '{"code": "Facility Code", "name": "Facility Name"}'},
    )
    assert response.status_code == 400 and "lat, lon" in response.json()["detail"]
    response = client.post(
        "/api/countries/1/imports/csv",
        files=_upload("x.csv", "a,b\n1,2\n"),
        data={"sheet": "Nodes", "mapping": '{"code": "nope"}'},
    )
    assert response.status_code == 400 and "no column called" in response.json()["detail"]
    response = client.post("/api/countries/1/imports/csv/inspect", files={"file": ("book.xlsx", b"PK", "application/octet-stream")})
    assert response.status_code == 400 and "workbook" in response.json()["detail"]
