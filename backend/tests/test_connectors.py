"""Connector tests, run against mock servers that reproduce each API's real shapes.

The claim these tests defend is narrow and worth stating: the connectors correctly
parse what these systems actually return, and live data goes through the same
validation and reconciliation as a spreadsheet. They do **not** claim the endpoints
have been confirmed against a ministry's live server — that is a Sprint 0 task per
country, and every connector reports `verified_against_live_instance = False` until
it has been.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.connectors import build, describe_all, get_connector_class
from app.connectors.base import ConnectorError
from app.connectors.dhis2 import DHIS2Connector
from app.connectors.msupply import MSupplyConnector
from app.connectors.openlmis import OpenLMISConnector
from app.connectors.reconcile import normalise_name, reconcile
from app.io.validation import validate_dataset
from app.seed.png import BOUNDARY as PNG_BOUNDARY
from app.models import Node

from . import lmis_fixtures as fixtures

BBOX = {"min_lat": -11.9, "max_lat": -1.0, "min_lon": 140.5, "max_lon": 160.2}


def dhis2(**kwargs) -> DHIS2Connector:
    transport = kwargs.pop("transport", None) or fixtures.dhis2_transport(**kwargs)
    return DHIS2Connector(
        base_url="https://dhis2.example.org",
        username="analyst",
        secret="secret",
        config={"product_map": fixtures.DHIS2_DATA_ELEMENTS, "page_size": 3},
        transport=transport,
    )


def openlmis(**kwargs) -> OpenLMISConnector:
    transport = kwargs.pop("transport", None) or fixtures.openlmis_transport(**kwargs)
    return OpenLMISConnector(
        base_url="https://openlmis.example.org",
        username="analyst",
        secret="secret",
        config={"page_size": 4, "consumption_path": "/api/consumption"},
        transport=transport,
    )


def msupply(**kwargs) -> MSupplyConnector:
    transport = kwargs.pop("transport", None) or fixtures.msupply_transport(**kwargs)
    return MSupplyConnector(
        base_url="https://msupply.example.org",
        username="analyst",
        secret="secret",
        config={"page_size": 4},
        transport=transport,
    )


# --- registry -------------------------------------------------------------------------


def test_every_registered_system_documents_itself():
    for described in describe_all():
        assert described["label"] and described["description"]
        assert described["docs_url"].startswith("http")
        assert described["auth_types"]
        for key, spec in described["config_spec"].items():
            assert spec["help"], f"{described['system']}.{key} has no help text"


def test_no_connector_claims_live_verification_it_does_not_have():
    """If one of these ever flips to True, it must be because somebody ran it."""
    for described in describe_all():
        assert described["verified_against_live_instance"] is False


def test_unknown_system_says_what_is_available():
    with pytest.raises(ConnectorError) as exc:
        get_connector_class("logistimo")
    assert "Excel importer" in exc.value.hint


# --- DHIS2 ------------------------------------------------------------------------------


def test_dhis2_test_reports_each_step_separately():
    info = dhis2().test()
    assert info.ok
    assert info.version == "2.41.1"
    names = {check.name: check for check in info.checks}
    assert names["Reached the server and authenticated"].ok
    assert "Level 4 is 'Facility'" in names["Facility level 4 exists"].detail


def test_dhis2_test_distinguishes_auth_failure_from_unreachable():
    unauthorised = dhis2(unauthorised=True).test()
    assert not unauthorised.ok
    assert "credentials" in unauthorised.checks[0].detail

    # test() is contracted never to raise: an operator needs a report, not a stack trace.
    unreachable = DHIS2Connector(
        base_url="https://dhis2.example.org", transport=fixtures.unreachable_transport()
    ).test()
    assert not unreachable.ok
    assert "Could not reach" in unreachable.detail
    assert "ministry intranet" in unreachable.detail


def test_dhis2_html_login_page_is_explained_not_just_rejected():
    connector = DHIS2Connector(
        base_url="https://dhis2.example.org", transport=fixtures.html_login_page_transport()
    )
    info = connector.test()
    assert not info.ok
    assert "not JSON" in info.detail
    assert "web interface" in info.checks[0].detail


def test_dhis2_pages_through_all_organisation_units():
    """The mock honours a page size of 3, so eight facilities need three pages."""
    result = dhis2().fetch()
    assert len(result.nodes) == len(fixtures.PNG_FACILITIES)
    assert {n["code"] for n in result.nodes} == {row[1] for row in fixtures.PNG_FACILITIES}


@pytest.mark.parametrize("legacy", [False, True])
def test_dhis2_reads_both_coordinate_representations(legacy):
    """`geometry` on 2.32+, a JSON string on older versions. Both are longitude-first."""
    result = dhis2(legacy_coordinates=legacy).fetch(include_demand=False)
    moresby = next(n for n in result.nodes if n["code"] == "PNG-NCD-001")
    assert moresby["lat"] == pytest.approx(-9.4747)
    assert moresby["lon"] == pytest.approx(147.1925)


def test_dhis2_keeps_facilities_without_coordinates_and_says_so():
    """Dropping them would hide part of the national list; the validator must see them."""
    result = dhis2().fetch()
    losuia = next(n for n in result.nodes if n["code"] == "PNG-MBP-002")
    assert losuia["lat"] is None
    assert losuia["geocode_confidence"] == 0.0
    assert any("no coordinate in DHIS2" in w for w in result.warnings)


def test_dhis2_can_be_told_to_skip_uncoded_facilities():
    connector = dhis2()
    connector.config["skip_facilities_without_coordinates"] = True
    result = connector.fetch(include_demand=False)
    assert all(n["lat"] is not None for n in result.nodes)


def test_dhis2_derives_province_and_district_from_the_parent_chain():
    result = dhis2().fetch(include_demand=False)
    node = next(n for n in result.nodes if n["code"] == "PNG-NCD-001")
    assert node["admin2"].endswith("District")
    assert node["admin1"].endswith("Province")


def test_dhis2_stores_the_uid_so_the_next_sync_matches_on_it():
    result = dhis2().fetch(include_demand=False)
    node = next(n for n in result.nodes if n["code"] == "PNG-NCD-001")
    assert node["external_ids"]["dhis2_uid"] == "dhisUID0001"


def test_dhis2_sums_analytics_periods_into_an_annual_total():
    """The mock splits each figure across two months, 40% then 60%."""
    result = dhis2().fetch()
    rows = {(d["node"], d["product"]): d["quantity"] for d in result.demand}
    assert rows[("PNG-NCD-001", "ESSMED-KIT")] == pytest.approx(5200.0)
    assert rows[("PNG-MAD-001", "VAC-EPI")] == pytest.approx(16800.0)


def test_dhis2_reports_analytics_rows_for_unknown_org_units():
    result = dhis2().fetch()
    assert any("not in the facility list" in w for w in result.warnings)


def test_dhis2_imports_no_consumption_without_a_product_map():
    """An unmapped data element is a number whose units nobody has checked."""
    connector = dhis2()
    connector.config["product_map"] = {}
    result = connector.fetch()
    assert result.demand == []
    assert result.nodes


def test_dhis2_analytics_failure_names_the_step():
    with pytest.raises(ConnectorError) as exc:
        dhis2(fail_analytics=True).fetch()
    assert exc.value.step == "analytics"


# --- OpenLMIS ----------------------------------------------------------------------------


def test_openlmis_authenticates_with_the_separate_client_credential():
    info = openlmis().test()
    assert info.ok
    assert info.checks[0].ok


def test_openlmis_blames_the_client_secret_when_the_token_is_missing():
    """OpenLMIS answers 200 with no token, so a status check would miss this."""
    info = openlmis(bad_client_secret=True).test()
    assert not info.ok
    assert "client id or client secret" in info.checks[0].detail


def test_openlmis_pages_and_skips_inactive_facilities():
    result = openlmis().fetch()
    codes = {n["code"] for n in result.nodes}
    assert codes == {row[1] for row in fixtures.PNG_FACILITIES}
    assert "PNG-OFF-001" not in codes
    assert any("inactive or disabled" in w for w in result.warnings)


def test_openlmis_reads_geographic_zone_as_district_and_province():
    result = openlmis().fetch(include_demand=False)
    node = next(n for n in result.nodes if n["code"] == "PNG-NCD-001")
    assert node["admin2"].endswith("District")
    assert node["admin1"].endswith("Province")


def test_openlmis_products_carry_no_volume_and_the_warning_says_why():
    result = openlmis().fetch()
    assert {p["sku"] for p in result.products} == {"ESSMED-KIT", "VAC-EPI"}
    assert all(p["volume_per_unit_cm3"] is None for p in result.products)
    assert any("volumetric" in w for w in result.warnings)


def test_openlmis_consumption_uses_the_configured_extraction_keys():
    result = openlmis().fetch()
    rows = {(d["node"], d["product"]): d["quantity"] for d in result.demand}
    assert rows[("PNG-MOR-001", "ESSMED-KIT")] == pytest.approx(5800.0)
    assert any("not in the imported list" in w for w in result.warnings)


def test_openlmis_wrong_extraction_keys_say_which_ones_to_fix():
    connector = openlmis()
    connector.config["consumption_facility_key"] = "facility.uuid"
    result = connector.fetch()
    assert result.demand == []
    assert any("facility.uuid" in w for w in result.warnings)


def test_openlmis_without_a_consumption_endpoint_still_imports_facilities():
    connector = openlmis()
    connector.config["consumption_path"] = ""
    result = connector.fetch()
    assert result.nodes and result.demand == []
    info = connector.test()
    assert any("no single consumption endpoint" in c.detail for c in info.checks)


# --- Open mSupply -------------------------------------------------------------------------


def test_msupply_authenticates_and_lists_stores():
    info = msupply().test()
    assert info.ok
    assert any("Badili" in check.detail for check in info.checks)


def test_msupply_reports_the_legacy_server_caveat_in_the_test():
    """PNG runs legacy mSupply, which has no GraphQL API. Saying so is the point."""
    info = msupply().test()
    flavour = next(c for c in info.checks if c.name == "Server flavour")
    assert "Legacy mSupply" in flavour.detail
    assert "Excel importer" in flavour.detail


def test_msupply_bad_password_is_read_from_the_graphql_body():
    info = msupply(bad_password=True).test()
    assert not info.ok
    assert "Incorrect username or password" in info.checks[0].detail


def test_msupply_schema_drift_is_a_clear_error_not_a_crash():
    """GraphQL answers 200 with an errors array; the connector must notice."""
    with pytest.raises(ConnectorError) as exc:
        msupply(schema_drift=True).fetch()
    assert "isStore" in str(exc.value)
    assert "editable" in exc.value.hint


def test_msupply_excludes_warehouses_from_the_facility_list():
    result = msupply().fetch()
    assert "NMS" not in {n["code"] for n in result.nodes}
    assert any("warehouses rather than facilities" in w for w in result.warnings)


def test_msupply_facilities_have_no_coordinates_and_the_warning_is_explicit():
    result = msupply().fetch()
    assert result.nodes
    assert all(n["lat"] is None for n in result.nodes)
    assert any("does not hold them" in w for w in result.warnings)


# --- live data goes through the same validator -----------------------------------------------


@pytest.mark.parametrize("factory", [dhis2, openlmis], ids=["dhis2", "openlmis"])
def test_a_facility_in_open_water_is_rejected_whatever_system_it_came_from(factory):
    """The central claim of the integration design, as an assertion."""
    result = factory().fetch()
    report = validate_dataset(
        country_code="PNG",
        bbox=BBOX,
        boundary=PNG_BOUNDARY,
        nodes=result.nodes,
        edges=[],
        products=result.products,
        demand=result.demand,
    )
    offshore = [i for i in report.issues if i.code == "node.offshore"]
    assert len(offshore) == 1
    assert "PNG-BAD-001" in offshore[0].entity
    assert report.blocking


def test_live_data_gets_the_same_actionable_messages_as_a_spreadsheet():
    result = dhis2().fetch()
    report = validate_dataset(
        country_code="PNG", bbox=BBOX, boundary=PNG_BOUNDARY, nodes=result.nodes, edges=[],
        products=result.products, demand=result.demand,
    )
    for issue in report.issues:
        assert issue.suggestion.strip(), issue.code


# --- reconciliation ----------------------------------------------------------------------------


def test_normalise_name_ignores_the_words_every_facility_shares():
    assert normalise_name("Kerema General Hospital") == normalise_name("Kerema Hospital")
    assert normalise_name("Losuia Health Centre") != normalise_name("Kavieng Health Centre")


@pytest.fixture
def png_nodes(session):
    return list(session.scalars(select(Node)))


def test_reconciliation_matches_on_the_system_key_before_anything_else(session, png_nodes):
    # Seed a stored DHIS2 UID on one facility, then rename and recode it upstream.
    node = next(n for n in png_nodes if n.code == "PNG-NCD-001")
    node.external_ids = {**(node.external_ids or {}), "dhis2_uid": "dhisUID0001"}
    session.flush()

    incoming = [
        {
            "code": "TOTALLY-DIFFERENT",
            "name": "Renamed Beyond Recognition",
            "lat": -9.4747,
            "lon": 147.1925,
            "external_ids": {"dhis2_uid": "dhisUID0001"},
        }
    ]
    result = reconcile(incoming, png_nodes, "dhis2")
    assert len(result.matched) == 1
    assert result.matched[0].method == "system_id"
    assert result.matched[0].existing_code == "PNG-NCD-001"
    session.rollback()


def test_reconciliation_classifies_the_whole_dhis2_pull(session, png_nodes):
    result = dhis2().fetch(include_demand=False)
    matching = reconcile(result.nodes, png_nodes, "dhis2")
    summary = matching.summary()

    matched_codes = {m.existing_code for m in matching.matched}
    assert {"PNG-NCD-001", "PNG-MOR-001", "PNG-MAD-001", "PNG-EHP-001", "PNG-NIP-001"} <= matched_codes
    assert {m.incoming_code for m in matching.new} == {"PNG-NEW-001", "PNG-BAD-001"}

    assert summary["moved"] == 1
    assert summary["renamed"] == 1
    assert summary["by_method"]["code"] >= 5
    assert "matched" in matching.headline()


def test_reconciliation_reports_how_far_a_facility_moved(session, png_nodes):
    result = dhis2().fetch(include_demand=False)
    matching = reconcile(result.nodes, png_nodes, "dhis2")
    kavieng = next(m for m in matching.matched if m.existing_code == "PNG-NIP-001")
    assert 2.0 < kavieng.distance_moved_km < 6.0
    assert "lat" in kavieng.changes


def test_reconciliation_never_proposes_to_erase_what_the_source_does_not_know(session, png_nodes):
    """An LMIS has no view of storage or hub economics; a sync must not touch them."""
    result = dhis2().fetch(include_demand=False)
    matching = reconcile(result.nodes, png_nodes, "dhis2")
    for match in matching.matched:
        assert not {"capacity", "hub_throughput_m3", "hub_fixed_cost", "terrain_class"} & set(match.changes)


def test_reconciliation_flags_two_records_claiming_the_same_facility(session, png_nodes):
    incoming = [
        {"code": "PNG-NCD-001", "name": "Port Moresby General Hospital", "lat": -9.47, "lon": 147.19,
         "external_ids": {}},
        {"code": "PNG-NCD-001", "name": "Port Moresby General Hospital (duplicate)", "lat": -9.47,
         "lon": 147.19, "external_ids": {}},
    ]
    matching = reconcile(incoming, png_nodes, "dhis2")
    assert len(matching.collisions) == 1
    assert "guess" in matching.collisions[0]["detail"]


def test_reconciliation_ignores_hubs_when_listing_what_is_absent(session, png_nodes):
    """Area Medical Stores are network structure, not LMIS records."""
    matching = reconcile([], png_nodes, "dhis2")
    absent_codes = {row["code"] for row in matching.absent}
    assert not any(code.startswith("AMS-") or code.startswith("NMS-") for code in absent_codes)
