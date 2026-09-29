"""Scenario items: data changes applied on top of the baseline, without touching it.

A what-if is not only a lever. "Close Wewak", "demand up a fifth in Morobe", "open a
store at Kimbe with a road lane to each of these clinics" are changes to the data, and
a scenario that carries them as items keeps the baseline exactly as loaded: the items
are applied to copies of the rows in memory when the scenario runs, and never written
back. The lever diff lists them beside the levers, so what an option changes is one
list, and the ledger keeps the base data honest.

Views are plain objects with the same attribute names as the model rows, so the
runner reads them exactly as it reads the rows themselves.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import inspect as sa_inspect

from .distance import resolve_distance

KINDS = ("close_facility", "set_node_field", "scale_demand", "add_node", "add_lane", "remove_lane", "set_lane_field")

NODE_FIELDS = {
    "name", "level", "type", "lat", "lon", "admin1", "admin2", "terrain_class", "catchment_population",
    "operating_status", "capacity", "hub_capable", "hub_fixed_cost", "hub_open_capex", "hub_throughput_m3",
}
LANE_FIELDS = {
    "mode", "distance_km", "base_travel_time_hr", "service_name", "service_frequency", "capacity_per_trip_m3",
    "cold_capacity_per_trip_m3", "fixed_cost_per_trip", "variable_cost_per_km", "cost_per_m3", "reliability",
    "lead_time_sd_days", "active", "monthly_access",
}


def _view(obj) -> SimpleNamespace:
    """A detached copy of a mapped row: every column, no session."""
    data = {attr.key: copy.deepcopy(getattr(obj, attr.key)) for attr in sa_inspect(type(obj)).column_attrs}
    return SimpleNamespace(**data)


def _node_view(**fields) -> SimpleNamespace:
    base = dict(
        id=None, country_id=None, code="", name="", level=1, type="area_store", lat=None, lon=None,
        geocode_confidence=0.6, geocode_source="scenario", admin1=None, admin2=None,
        capacity={}, operating_status="planned", catchment_population=0.0, terrain_class="mainland_road",
        hub_capable=True, hub_fixed_cost=0.0, hub_open_capex=0.0, hub_throughput_m3=0.0,
        external_ids={}, attributes={}, derivations={}, retired_at=None, retired_reason="", last_import=None,
    )
    base.update({k: v for k, v in fields.items() if k in base})
    return SimpleNamespace(**base)


def _edge_view(**fields) -> SimpleNamespace:
    base = dict(
        id=None, country_id=None, code="", from_node_id=None, to_node_id=None, mode="road",
        distance_km=0.0, base_travel_time_hr=0.0, distance_method="detour_factor", distance_confidence=0.5,
        distance_note=None, service_frequency=None, service_days=None, service_name=None,
        capacity_per_trip_m3=0.0, cold_capacity_per_trip_m3=0.0, fixed_cost_per_trip=0.0,
        variable_cost_per_km=0.0, cost_per_m3=0.0, monthly_access=[1.0] * 12, monthly_cost_multiplier=[1.0] * 12,
        reliability=0.9, lead_time_sd_days=2.0, active=True, attributes={}, retired_at=None, retired_reason="",
        last_import=None, from_node=None, to_node=None,
    )
    base.update({k: v for k, v in fields.items() if k in base})
    return SimpleNamespace(**base)


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, dict):
        return "; ".join(f"{k} {_fmt(v)}" for k, v in value.items())
    return str(value)


def describe(item: dict, names: Optional[Dict[str, str]] = None) -> str:
    """One line a person can read, for the lever diff and the scenario card."""
    names = names or {}
    kind = item.get("kind")
    code = item.get("code", "")
    who = names.get(code, code)
    if kind == "close_facility":
        return f"close {who}"
    if kind == "set_node_field":
        return f"{who}: {str(item.get('field', '')).replace('_', ' ')} → {_fmt(item.get('value'))}"
    if kind == "scale_demand":
        scope = []
        if item.get("admin1"):
            scope.append(f"in {item['admin1']}")
        if item.get("sku"):
            scope.append(f"for {item['sku']}")
        if item.get("codes"):
            scope.append(f"at {len(item['codes'])} facilities")
        return f"demand × {float(item.get('factor', 1.0)):g}" + (" " + " ".join(scope) if scope else "")
    if kind == "add_node":
        return f"add store {item.get('name') or code}" + (f" at {item.get('lat'):.2f}, {item.get('lon'):.2f}" if item.get("lat") is not None else "")
    if kind == "add_lane":
        return f"add lane {item.get('from_code')} → {item.get('to_code')} by {item.get('mode', 'road')}"
    if kind == "remove_lane":
        return f"remove lane {code}"
    if kind == "set_lane_field":
        return f"lane {code}: {str(item.get('field', '')).replace('_', ' ')} → {_fmt(item.get('value'))}"
    return f"{kind}"


def validate(items: Iterable[dict], node_codes: set, edge_codes: set, skus: set) -> List[str]:
    """Problems that would make an item meaningless, in words. Empty means fine."""
    problems: List[str] = []
    known_nodes = set(node_codes)
    known_edges = set(edge_codes)
    for index, item in enumerate(items, start=1):
        kind = item.get("kind")
        if kind not in KINDS:
            problems.append(f"Item {index}: unknown kind '{kind}'. Choose from {', '.join(KINDS)}.")
            continue
        if kind in ("close_facility", "set_node_field") and item.get("code") not in known_nodes:
            problems.append(f"Item {index}: no facility with code {item.get('code')!r}.")
        if kind == "set_node_field" and item.get("field") not in NODE_FIELDS:
            problems.append(f"Item {index}: '{item.get('field')}' is not a field a scenario can set on a facility.")
        if kind == "scale_demand":
            try:
                factor = float(item.get("factor"))
            except (TypeError, ValueError):
                factor = -1
            if factor < 0:
                problems.append(f"Item {index}: demand needs a factor of zero or more.")
            if item.get("sku") and item["sku"] not in skus:
                problems.append(f"Item {index}: no product with SKU {item['sku']!r}.")
            for code in item.get("codes") or []:
                if code not in known_nodes:
                    problems.append(f"Item {index}: no facility with code {code!r}.")
        if kind == "add_node":
            if not item.get("code") or item.get("lat") is None or item.get("lon") is None:
                problems.append(f"Item {index}: a new store needs a code, a latitude and a longitude.")
            elif item["code"] in known_nodes:
                problems.append(f"Item {index}: a facility with code {item['code']!r} already exists.")
            else:
                known_nodes.add(item["code"])
        if kind == "add_lane":
            if item.get("from_code") not in known_nodes or item.get("to_code") not in known_nodes:
                problems.append(f"Item {index}: a lane needs two facilities that exist (or are added above it).")
            elif item.get("code") in known_edges:
                problems.append(f"Item {index}: a lane with code {item.get('code')!r} already exists.")
            else:
                known_edges.add(item.get("code") or f"{item.get('from_code')}-{item.get('to_code')}")
        if kind in ("remove_lane", "set_lane_field") and item.get("code") not in known_edges:
            problems.append(f"Item {index}: no lane with code {item.get('code')!r}.")
        if kind == "set_lane_field" and item.get("field") not in LANE_FIELDS:
            problems.append(f"Item {index}: '{item.get('field')}' is not a field a scenario can set on a lane.")
    return problems


def apply(
    items: List[dict],
    nodes: list,
    edges: list,
    demand_rows: list,
    products: dict,
    *,
    config: Optional[dict] = None,
) -> Tuple[list, list, list, List[str]]:
    """Copies of the rows with the items applied. The originals are not touched."""
    if not items:
        return nodes, edges, demand_rows, []
    node_views = {n.id: _view(n) for n in nodes}
    edge_views = {e.id: _view(e) for e in edges}
    demand_views = [_view(d) for d in demand_rows]
    by_code = {v.code: v for v in node_views.values()}
    edge_by_code = {v.code: v for v in edge_views.values()}
    sku_to_id = {p.sku: pid for pid, p in products.items()}
    next_id = -1
    notes: List[str] = []

    for item in items:
        kind = item.get("kind")
        if kind == "close_facility":
            node = by_code.get(item.get("code"))
            if node is None:
                continue
            node_views.pop(node.id, None)
            by_code.pop(node.code, None)
            for eid in [eid for eid, e in edge_views.items() if e.from_node_id == node.id or e.to_node_id == node.id]:
                edge_by_code.pop(edge_views[eid].code, None)
                edge_views.pop(eid)
            demand_views = [d for d in demand_views if d.node_id != node.id]
            notes.append(describe(item))
        elif kind == "set_node_field":
            node = by_code.get(item.get("code"))
            if node is None or item.get("field") not in NODE_FIELDS:
                continue
            setattr(node, item["field"], item.get("value"))
            notes.append(describe(item))
        elif kind == "scale_demand":
            factor = float(item.get("factor", 1.0))
            codes = set(item.get("codes") or [])
            sku_id = sku_to_id.get(item.get("sku")) if item.get("sku") else None
            admin1 = item.get("admin1")
            for row in demand_views:
                node = node_views.get(row.node_id)
                if node is None:
                    continue
                if codes and node.code not in codes:
                    continue
                if admin1 and (node.admin1 or "") != admin1:
                    continue
                if sku_id is not None and row.product_id != sku_id:
                    continue
                row.quantity = float(row.quantity or 0.0) * factor
            notes.append(describe(item))
        elif kind == "add_node":
            if item.get("code") in by_code:
                continue
            node = _node_view(**item)
            node.id = next_id
            next_id -= 1
            node_views[node.id] = node
            by_code[node.code] = node
            notes.append(describe(item))
        elif kind == "add_lane":
            origin, target = by_code.get(item.get("from_code")), by_code.get(item.get("to_code"))
            if origin is None or target is None:
                continue
            code = item.get("code") or f"{origin.code}-{target.code}-{item.get('mode', 'road')}"
            if code in edge_by_code:
                continue
            distance = resolve_distance(
                origin.lat, origin.lon, target.lat, target.lon,
                mode=item.get("mode", "road"), terrain_class=getattr(target, "terrain_class", "mainland_road"),
                manual_km=item.get("distance_km"), manual_hours=item.get("base_travel_time_hr"),
                manual_note="scenario item", config=config,
            )
            edge = _edge_view(**{k: v for k, v in item.items() if k in LANE_FIELDS}, code=code, **distance.as_edge_fields())
            edge.id = next_id
            next_id -= 1
            edge.from_node_id, edge.to_node_id = origin.id, target.id
            edge_views[edge.id] = edge
            edge_by_code[code] = edge
            notes.append(describe(item))
        elif kind == "remove_lane":
            edge = edge_by_code.pop(item.get("code"), None)
            if edge is not None:
                edge_views.pop(edge.id, None)
                notes.append(describe(item))
        elif kind == "set_lane_field":
            edge = edge_by_code.get(item.get("code"))
            if edge is None or item.get("field") not in LANE_FIELDS:
                continue
            setattr(edge, item["field"], item.get("value"))
            notes.append(describe(item))

    # Wire the relationship attributes the runner reads, on the views.
    for edge in list(edge_views.values()):
        edge.from_node = node_views.get(edge.from_node_id)
        edge.to_node = node_views.get(edge.to_node_id)
    edges_out = [e for e in edge_views.values() if e.from_node is not None and e.to_node is not None]
    return list(node_views.values()), edges_out, demand_views, notes
