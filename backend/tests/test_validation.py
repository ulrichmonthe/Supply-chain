"""Tests for the validation report.

These assert on the *content* of messages, not just their presence. A validation
report whose text a provincial data officer cannot act on has failed at its job, so
the wording is part of the contract.
"""

from __future__ import annotations

from app.io.validation import ERROR, WARNING, validate_dataset

BBOX = {"min_lat": -11.9, "max_lat": -1.0, "min_lon": 140.5, "max_lon": 160.2}


def _node(**overrides):
    base = {
        "_row": 2,
        "code": "F1",
        "name": "Kupiano Health Centre",
        "lat": -10.0667,
        "lon": 148.1833,
        "admin1": "Central",
        "terrain_class": "coastal_road",
        "catchment_population": 38000,
        "capacity": {"dry_m3": 10.0},
        "hub_capable": "FALSE",
    }
    base.update(overrides)
    return base


def _product(**overrides):
    base = {
        "_row": 2,
        "sku": "ESSMED-KIT",
        "name": "Essential medicines kit",
        "temperature_band": "ambient",
        "volume_per_unit_cm3": 62000.0,
    }
    base.update(overrides)
    return base


def _run(nodes=None, edges=None, products=None, demand=None):
    return validate_dataset(
        country_code="PNG",
        bbox=BBOX,
        nodes=nodes if nodes is not None else [_node()],
        edges=edges or [],
        products=products if products is not None else [_product()],
        demand=demand or [],
    )


def _codes(report, severity=None):
    return {i.code for i in report.issues if severity is None or i.severity == severity}


def test_clean_data_passes():
    report = _run(
        nodes=[_node(code="HUB", hub_capable="TRUE", capacity={"dry_m3": 900.0})],
        edges=[
            {
                "_row": 2,
                "code": "L1",
                "from_node": "HUB",
                "to_node": "HUB",
                "mode": "road",
            }
        ],
    )
    # The self-loop is the only intended error here; prove the others do not fire.
    assert _codes(report, ERROR) == {"edge.self_loop"}


def test_facility_in_the_bismarck_sea_is_an_error_that_says_so():
    report = _run(nodes=[_node(lat=-3.5, lon=149.0)])
    issue = next(i for i in report.issues if i.code == "node.offshore")
    assert issue.severity == ERROR
    assert "offshore" in issue.message
    assert "km" in issue.message
    assert issue.suggestion
    assert report.blocking


def test_swapped_coordinates_are_detected_and_the_fix_is_given():
    report = _run(nodes=[_node(lat=147.1925, lon=-9.4747)])
    issue = next(i for i in report.issues if i.code == "node.swapped_coordinates")
    assert "swapped" in issue.suggestion
    assert "-9.4747" in issue.suggestion


def test_null_island_is_caught_before_the_bbox_check():
    report = _run(nodes=[_node(lat=0.0, lon=0.0)])
    assert "node.null_island" in _codes(report)
    assert "node.outside_country" not in _codes(report)


def test_whole_degree_coordinates_are_flagged_as_a_placeholder():
    report = _run(nodes=[_node(lat=-9.0, lon=147.0)])
    assert "node.low_precision" in _codes(report, WARNING)


def test_duplicate_facility_code_is_an_error():
    report = _run(nodes=[_node(), _node(_row=3, name="Another")])
    assert "node.duplicate_code" in _codes(report, ERROR)


def test_province_outlier_is_flagged():
    """A Central Province facility sitting up in the Sepik should not pass quietly."""
    central = [
        _node(_row=r, code=f"C{r}", lat=lat, lon=lon)
        for r, (lat, lon) in enumerate(
            [(-9.83, 147.73), (-8.65, 146.50), (-10.07, 148.18), (-8.90, 146.65)], start=2
        )
    ]
    stray = _node(_row=99, code="C99", name="Stray Health Centre", lat=-3.57, lon=143.67)
    report = _run(nodes=[*central, stray])
    issue = next(i for i in report.issues if i.code == "node.province_outlier")
    assert "Stray Health Centre" in issue.message
    assert "Central" in issue.message


def test_missing_population_warns_because_equity_depends_on_it():
    report = _run(nodes=[_node(catchment_population=0)])
    issue = next(i for i in report.issues if i.code == "node.no_population")
    assert "equity" in issue.suggestion.lower()


def test_edge_to_unknown_node_is_an_error():
    report = _run(
        edges=[{"_row": 2, "code": "L1", "from_node": "F1", "to_node": "GHOST", "mode": "road"}]
    )
    assert "edge.unknown_destination" in _codes(report, ERROR)


def test_vessel_without_a_hold_or_a_rate_is_an_error():
    report = _run(
        edges=[
            {
                "_row": 2,
                "code": "L1",
                "from_node": "F1",
                "to_node": "F1",
                "mode": "sea",
                "service_frequency": "WEEKLY",
            }
        ]
    )
    assert "edge.scheduled_without_capacity" in _codes(report, ERROR)


def test_chartered_aircraft_priced_per_m3_needs_no_hold():
    report = _run(
        nodes=[_node(), _node(_row=3, code="F2", name="Telefomin", lat=-5.1167, lon=141.6333)],
        edges=[
            {
                "_row": 2,
                "code": "L1",
                "from_node": "F1",
                "to_node": "F2",
                "mode": "air",
                "service_frequency": "MONTHLY",
                "cost_per_m3": 2800.0,
            }
        ],
    )
    assert "edge.scheduled_without_capacity" not in _codes(report)


def test_road_lane_on_a_rhythm_needs_no_hold():
    report = _run(
        nodes=[_node(), _node(_row=3, code="F2", name="Kwikila", lat=-9.8322, lon=147.7331)],
        edges=[
            {
                "_row": 2,
                "code": "L1",
                "from_node": "F1",
                "to_node": "F2",
                "mode": "road",
                "service_frequency": "MONTHLY",
            }
        ],
    )
    assert "edge.scheduled_without_capacity" not in _codes(report)


def test_access_vector_of_the_wrong_length_is_rejected():
    report = _run(
        edges=[
            {
                "_row": 2,
                "code": "L1",
                "from_node": "F1",
                "to_node": "F1",
                "mode": "road",
                "monthly_access": [1.0] * 11,
            }
        ]
    )
    assert "edge.bad_access_vector" in _codes(report, ERROR)


def test_percentage_style_access_values_get_a_useful_hint():
    report = _run(
        edges=[
            {
                "_row": 2,
                "code": "L1",
                "from_node": "F1",
                "to_node": "F1",
                "mode": "road",
                "monthly_access": [100.0] * 12,
            }
        ]
    )
    issue = next(i for i in report.issues if i.code == "edge.access_out_of_range")
    assert "divide by 100" in issue.suggestion


def test_demand_for_an_unknown_facility_is_an_error():
    report = _run(demand=[{"_row": 2, "node": "GHOST", "product": "ESSMED-KIT", "quantity": 10}])
    assert "demand.unknown_node" in _codes(report, ERROR)


def test_negative_demand_explains_the_likely_cause():
    report = _run(
        demand=[{"_row": 2, "node": "F1", "product": "ESSMED-KIT", "quantity": -5, "source": "actual"}]
    )
    issue = next(i for i in report.issues if i.code == "demand.negative")
    assert "stock adjustment" in issue.suggestion


def test_facility_with_demand_but_no_lane_is_an_error():
    report = _run(demand=[{"_row": 2, "node": "F1", "product": "ESSMED-KIT", "quantity": 10}])
    issue = next(i for i in report.issues if i.code == "coverage.unreachable_facilities")
    assert issue.severity == ERROR
    assert "F1" in issue.message


def test_product_without_volume_is_rejected():
    report = _run(products=[_product(volume_per_unit_cm3=None)])
    assert "product.no_volume" in _codes(report, ERROR)


def test_headline_distinguishes_blocking_from_reviewable():
    blocking = _run(nodes=[_node(lat=-3.5, lon=149.0)])
    assert "must be fixed" in blocking.headline()

    reviewable = _run(nodes=[_node(catchment_population=0)])
    assert not reviewable.blocking
    assert "should be reviewed" in reviewable.headline()


def test_every_issue_carries_a_suggestion():
    """The rule that makes this a product rather than a linter."""
    report = _run(
        nodes=[_node(lat=-3.5, lon=149.0), _node(_row=3, code="F2", catchment_population=0)],
        edges=[{"_row": 2, "code": "L1", "from_node": "F1", "to_node": "GHOST", "mode": "spaceship"}],
        products=[_product(temperature_band="cold")],
        demand=[{"_row": 2, "node": "GHOST", "product": "NOPE", "quantity": -1}],
    )
    assert len(report.issues) > 5
    for issue in report.issues:
        assert issue.suggestion.strip(), issue.code
        assert issue.message.strip(), issue.code
