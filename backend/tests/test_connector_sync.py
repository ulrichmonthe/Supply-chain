"""End-to-end: configure a connection, preview a sync, apply it.

The behaviour under test is the whole reason the integration is shaped the way it is:
a live pull is validated and reconciled like a spreadsheet, nothing is written until
somebody has looked, and applying it merges facilities **without touching the lanes**.

That last point is the one worth breaking a build over. DHIS2 has no idea which boat
calls at Losuia. If a sync from it dropped the timetables, it would delete the part of
the model that took a fortnight of interviews to assemble, and the tool would be worse
than the spreadsheet it replaced.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.connectors.dhis2 import DHIS2Connector
from app.db import get_session
from app.main import app
from app.models import AuditEntry, Connection, Demand, Edge, Node, Product

from . import lmis_fixtures as fixtures


@pytest.fixture
def seeded_session_factory(isolated_session_factory):
    """These tests commit, so they get a database nothing else reads."""
    return isolated_session_factory


@pytest.fixture
def client(seeded_session_factory, monkeypatch):
    """The API, wired to the seeded test database and a mock DHIS2."""

    def _session_override():
        session = seeded_session_factory()
        try:
            yield session
        finally:
            session.close()

    def _build(connection, **kwargs):
        return DHIS2Connector(
            base_url=connection.base_url or "https://dhis2.example.org",
            username=connection.username,
            secret="secret",
            config=connection.config or {},
            transport=fixtures.dhis2_transport(),
        )

    monkeypatch.setattr("app.api.connectors.connectors.build", _build)
    app.dependency_overrides[get_session] = _session_override
    # The app's own lifespan seeds a different database; the override is what the
    # endpoints actually use, so the client is constructed without running it.
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def connection(client, request):
    # The seeded database is shared across the suite and connection names are unique
    # per country, so each test gets its own rather than colliding on the second run.
    response = client.post(
        "/api/countries/1/connections",
        json={
            "name": f"NDoH DHIS2 [{request.node.name}]",
            "system": "dhis2",
            "base_url": "https://dhis2.example.org",
            "username": "analyst",
            "secret": "secret",
            "config": {"product_map": fixtures.DHIS2_DATA_ELEMENTS, "page_size": 3},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- configuration ------------------------------------------------------------------


def test_the_api_never_returns_the_credential(client, connection):
    assert "secret" not in connection
    assert connection["secret_set"] is True
    assert connection["secret_source"] == "stored"

    listed = client.get("/api/countries/1/connections").json()
    assert listed
    assert all("secret" not in row for row in listed)


def test_a_credential_can_live_in_the_environment_instead(client, monkeypatch):
    monkeypatch.setenv("DHIS2_TOKEN", "from-the-environment")
    created = client.post(
        "/api/countries/1/connections",
        json={
            "name": "DHIS2 via env",  # only created once, in this test

            "system": "dhis2",
            "base_url": "https://dhis2.example.org",
            "auth_type": "token",
            "secret_env": "DHIS2_TOKEN",
        },
    ).json()
    assert created["secret_set"] is True
    assert created["secret_source"] == "environment"


def test_an_unknown_system_is_rejected_with_the_list_of_known_ones(client):
    response = client.post(
        "/api/countries/1/connections", json={"name": "x", "system": "logistimo"}
    )
    assert response.status_code == 400
    assert "dhis2" in response.json()["detail"]


def test_connectors_are_advertised_as_read_only(client):
    payload = client.get("/api/connectors").json()
    assert {s["system"] for s in payload["systems"]} == {"dhis2", "msupply", "openlmis"}
    assert "read-only" in payload["note"]
    assert "no logistics system knows which boat calls" in payload["note"]


def test_the_connection_test_is_stored_for_later(client, connection):
    result = client.post(f"/api/connections/{connection['id']}/test").json()
    assert result["ok"] is True
    assert len(result["checks"]) >= 3

    stored = next(
        row for row in client.get("/api/countries/1/connections").json()
        if row["id"] == connection["id"]
    )
    assert stored["last_test_ok"] is True
    assert stored["last_tested_at"]


# --- preview -------------------------------------------------------------------------


@pytest.fixture
def preview(client, connection):
    response = client.post(f"/api/connections/{connection['id']}/preview")
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_writes_nothing(client, connection, seeded_session_factory):
    session = seeded_session_factory()
    before = session.scalar(select(Node).where(Node.code == "PNG-NIP-001"))
    before_lat, before_count = before.lat, session.query(Node).count()
    session.close()

    client.post(f"/api/connections/{connection['id']}/preview")

    session = seeded_session_factory()
    after = session.scalar(select(Node).where(Node.code == "PNG-NIP-001"))
    assert after.lat == before_lat
    assert session.query(Node).count() == before_count
    session.close()


def test_preview_runs_the_same_validator_as_a_spreadsheet(preview):
    """The Bismarck Sea facility in the mock must block the sync."""
    assert preview["blocking"] is True
    offshore = [i for i in preview["issues"] if i["code"] == "node.offshore"]
    assert len(offshore) == 1
    assert "offshore" in offshore[0]["message"]
    assert offshore[0]["suggestion"]


def test_preview_reconciles_against_what_is_already_there(preview):
    summary = preview["reconciliation"]["summary"]
    assert summary["matched"] >= 5
    assert summary["new"] >= 1
    assert summary["moved"] == 1
    assert summary["renamed"] == 1
    assert "matched" in preview["reconciliation"]["headline"]


def test_preview_surfaces_what_the_connector_could_not_do(preview):
    assert any("no coordinate in DHIS2" in w for w in preview["connector_warnings"])
    assert preview["stats"]["facilities"] == len(fixtures.PNG_FACILITIES)


def test_preview_says_lanes_will_not_be_touched(preview):
    assert preview["commit_mode"] == "merge"
    assert "Lanes are left" in preview["commit_note"]


def test_a_blocking_preview_cannot_be_committed(client, preview):
    response = client.post(f"/api/imports/{preview['batch_id']}/commit?replace=false")
    assert response.status_code == 409
    assert "errors that must be fixed" in response.json()["detail"]


# --- commit ---------------------------------------------------------------------------


@pytest.fixture
def clean_preview(client, connection, monkeypatch):
    """The same pull with the offshore facility removed, so it can be committed."""
    good = [row for row in fixtures.PNG_FACILITIES if row[1] != "PNG-BAD-001"]

    def _build(conn, **kwargs):
        return DHIS2Connector(
            base_url="https://dhis2.example.org",
            username=conn.username,
            secret="secret",
            config=conn.config or {},
            transport=fixtures.dhis2_transport(facilities=good),
        )

    monkeypatch.setattr("app.api.connectors.connectors.build", _build)
    response = client.post(f"/api/connections/{connection['id']}/preview")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["blocking"] is False, [i["message"] for i in payload["issues"] if i["severity"] == "error"]
    return payload


def test_commit_merges_facilities_and_leaves_the_network_alone(
    client, clean_preview, seeded_session_factory
):
    session = seeded_session_factory()
    edges_before = session.query(Edge).count()
    kavieng_before = session.scalar(select(Node).where(Node.code == "PNG-NIP-001"))
    lat_before, lon_before = kavieng_before.lat, kavieng_before.lon
    session.close()

    result = client.post(f"/api/imports/{clean_preview['batch_id']}/commit?replace=false")
    assert result.status_code == 200, result.text
    counts = result.json()

    assert counts["mode"] == "merge"
    assert counts["counts"]["nodes_created"] == 1  # the genuinely new clinic
    assert counts["counts"]["nodes_updated"] >= 1

    session = seeded_session_factory()
    # The transport network is untouched. This is the assertion that matters most.
    assert session.query(Edge).count() == edges_before

    kavieng = session.scalar(select(Node).where(Node.code == "PNG-NIP-001"))
    assert (kavieng.lat, kavieng.lon) != (lat_before, lon_before)
    assert kavieng.external_ids["dhis2_uid"] == "dhisUID0005"

    goroka = session.scalar(select(Node).where(Node.code == "PNG-EHP-001"))
    assert goroka.name == "Goroka Base Hospital"

    assert session.scalar(select(Node).where(Node.code == "PNG-NEW-001")) is not None
    session.close()


def test_commit_writes_back_the_source_key_so_the_next_sync_matches_on_it(
    client, clean_preview, seeded_session_factory
):
    client.post(f"/api/imports/{clean_preview['batch_id']}/commit?replace=false")
    session = seeded_session_factory()
    node = session.scalar(select(Node).where(Node.code == "PNG-NCD-001"))
    assert node.external_ids.get("dhis2_uid") == "dhisUID0001"
    session.close()


def test_commit_replaces_only_the_demand_the_pull_supplied(
    client, clean_preview, seeded_session_factory
):
    """A pull covering two products must not wipe the other six."""
    session = seeded_session_factory()
    moresby = session.scalar(select(Node).where(Node.code == "PNG-NCD-001"))
    other_before = (
        session.query(Demand)
        .join(Product, Demand.product_id == Product.id)
        .filter(Demand.node_id == moresby.id, Product.sku.notin_(["ESSMED-KIT", "VAC-EPI"]))
        .count()
    )
    assert other_before > 0
    session.close()

    client.post(f"/api/imports/{clean_preview['batch_id']}/commit?replace=false")

    session = seeded_session_factory()
    moresby = session.scalar(select(Node).where(Node.code == "PNG-NCD-001"))
    other_after = (
        session.query(Demand)
        .join(Product, Demand.product_id == Product.id)
        .filter(Demand.node_id == moresby.id, Product.sku.notin_(["ESSMED-KIT", "VAC-EPI"]))
        .count()
    )
    assert other_after == other_before

    essmed = (
        session.query(Demand)
        .join(Product, Demand.product_id == Product.id)
        .filter(Demand.node_id == moresby.id, Product.sku == "ESSMED-KIT")
        .one()
    )
    assert essmed.quantity == pytest.approx(5200.0)
    # And it is now measured consumption rather than a population proxy.
    assert essmed.source == "actual"
    session.close()


def test_commit_records_the_sync_in_the_audit_trail(client, clean_preview, seeded_session_factory):
    client.post(f"/api/imports/{clean_preview['batch_id']}/commit?replace=false")
    session = seeded_session_factory()
    entry = session.scalars(
        select(AuditEntry).where(AuditEntry.provenance == "lmis_sync").order_by(AuditEntry.id.desc())
    ).first()
    assert entry is not None
    assert entry.field == "dhis2_merge"
    assert "Lanes were not touched" in entry.rationale
    session.close()


def test_a_batch_cannot_be_applied_twice(client, clean_preview):
    assert client.post(f"/api/imports/{clean_preview['batch_id']}/commit?replace=false").status_code == 200
    second = client.post(f"/api/imports/{clean_preview['batch_id']}/commit?replace=false")
    assert second.status_code == 409
    assert "already been applied" in second.json()["detail"]


def test_a_second_sync_matches_on_the_stored_uid_not_the_code(
    client, connection, clean_preview, seeded_session_factory
):
    """After the first sync, upstream can recode a facility and it still matches."""
    client.post(f"/api/imports/{clean_preview['batch_id']}/commit?replace=false")

    recoded = [
        ("dhisUID0001", "NEW-CODE-999", "Port Moresby General Hospital", -9.4747, 147.1925, "recoded"),
    ]

    def _build(conn, **kwargs):
        return DHIS2Connector(
            base_url="https://dhis2.example.org",
            username=conn.username,
            secret="secret",
            config=conn.config or {},
            transport=fixtures.dhis2_transport(facilities=recoded),
        )

    import app.api.connectors as connectors_api

    connectors_api.connectors.build = _build
    payload = client.post(f"/api/connections/{connection['id']}/preview").json()
    matched = payload["reconciliation"]["matched"]
    assert len(matched) == 1
    assert matched[0]["method"] == "system_id"
    assert matched[0]["existing_code"] == "PNG-NCD-001"


def test_the_sync_stamp_can_be_recorded(client, connection):
    result = client.post(
        f"/api/connections/{connection['id']}/record-sync",
        json={"nodes_created": 1, "nodes_updated": 5},
    ).json()
    assert result["last_sync_at"]
    assert result["last_sync_summary"]["nodes_updated"] == 5
