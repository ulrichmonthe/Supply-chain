"""The CSV dump: the same tables as the workbook, readable by anything, importable back."""

from __future__ import annotations

import csv
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.io import excel_out, mapper
from app.io.schema_spec import SHEETS
from app.main import app


@pytest.fixture
def client(seeded_session_factory):
    def _session_override():
        session = seeded_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _rows(data: bytes):
    text = data.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    return header, list(reader)


def test_the_dump_holds_one_csv_per_table_in_the_template_columns(client):
    response = client.get("/api/countries/1/export/csv.zip")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert set(archive.namelist()) == {"nodes.csv", "edges.csv", "products.csv", "demand.csv", "README.txt"}
    nodes = client.get("/api/countries/1/nodes").json()
    edges = client.get("/api/countries/1/edges").json()
    for table, sheet, count in (("nodes", "Nodes", len(nodes)), ("edges", "Edges", len(edges)), ("products", "Products", 8)):
        header, rows = _rows(archive.read(f"{table}.csv"))
        assert header == [name for name, _ in SHEETS[sheet]], table
        assert len(rows) == count, table
    header, rows = _rows(archive.read("demand.csv"))
    assert header == [name for name, _ in SHEETS["Demand"]] and len(rows) > 1000
    assert "Columns:" in archive.read("README.txt").decode()


def test_one_table_downloads_on_its_own_and_matches_the_workbook(client):
    response = client.get("/api/countries/1/export/nodes.csv")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert 'filename="PNG-nodes.csv"' in response.headers["content-disposition"]
    header, rows = _rows(response.content)
    workbook = client.get("/api/countries/1/export/network.xlsx").content
    from app.io import excel_in

    parsed = excel_in.parse_workbook(workbook)
    assert len(rows) == len(parsed["nodes"])
    by_code = {row[0]: row for row in rows}
    pom = next(n for n in parsed["nodes"] if n["code"] == "PNG-NCD-001")
    assert float(by_code["PNG-NCD-001"][header.index("lat")]) == pytest.approx(pom["lat"])
    assert by_code["PNG-NCD-001"][header.index("hub_capable")] == "FALSE"
    assert client.get("/api/countries/1/export/nothing.csv").status_code == 404


def test_a_table_csv_goes_back_in_through_the_mapper(client):
    data = client.get("/api/countries/1/export/nodes.csv").content
    columns, rows, delimiter = mapper.read_csv(data)
    assert delimiter == "," and mapper.guess_sheet(columns) == "Nodes"
    guess = mapper.guess_mapping(columns, "Nodes")
    # Our own column names map to themselves: nothing to confirm.
    assert all(guess[field] == field for field in ("code", "name", "lat", "lon", "catchment_population"))
    inspect = client.post("/api/countries/1/imports/csv/inspect", files={"file": ("nodes.csv", data, "text/csv")})
    assert inspect.status_code == 200 and inspect.json()["sheet"] == "Nodes" and inspect.json()["row_count"] == len(rows)


def test_the_header_counts_a_facility_before_it_has_demand(client):
    before = client.get("/api/countries/1/overview").json()["counts"]["facilities"]
    created = client.post(
        "/api/countries/1/nodes",
        json={"code": "CSV-NEW-1", "name": "New Clinic", "lat": -6.1, "lon": 145.4, "catchment_population": 500, "confidence_marker": "I", "reason": "test"},
        headers={"X-Author": "Tester"},
    )
    assert created.status_code == 201
    try:
        assert client.get("/api/countries/1/overview").json()["counts"]["facilities"] == before + 1
    finally:
        client.post(f"/api/nodes/{created.json()['node']['id']}/retire", json={"reason": "test over"}, headers={"X-Author": "Tester"})
