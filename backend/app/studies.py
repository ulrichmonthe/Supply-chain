"""Studies: comparison as a workflow, not a tab.

A study is a question -- "Can we close Wewak?", "Land, air or sea?" -- and the ordered
scenarios that answer it, with the baseline always first. It owns the compare view
(what differs between the options and what that did), the diff map (one network, the
lanes only one option uses drawn in its colour), and the decision page of its report.

Presets generate the classic studies in one click, as scenarios cloned from the
baseline with one thing changed each, so the lever diff is short and the answer is
attributable.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger
from .engine import confidence as confidence_mod
from .engine import kpis as kpi_mod
from .engine import seasonality
from .engine.runner import run_scenario
from .models import Country, Edge, Result, Scenario, Study
from .tagging import normalise_tags

# --- presets -----------------------------------------------------------------------


def _unique_name(session: Session, country_id: int, name: str) -> str:
    candidate, suffix = name, 2
    while session.scalar(select(Scenario).where(Scenario.country_id == country_id, Scenario.name == candidate)):
        candidate = f"{name} ({suffix})"
        suffix += 1
    return candidate


def _clone(
    session: Session,
    baseline: Scenario,
    *,
    name: str,
    description: str,
    levers: Optional[dict] = None,
    constraints: Optional[dict] = None,
    weights: Optional[dict] = None,
    tags: Optional[List[str]] = None,
) -> Scenario:
    scenario = Scenario(
        country_id=baseline.country_id,
        name=_unique_name(session, baseline.country_id, name),
        description=description,
        parent_scenario_id=baseline.id,
        is_baseline=False,
        tags=normalise_tags(list(baseline.tags or []) + (tags or ["study"])),
        levers={**(baseline.levers or {}), **(levers or {})},
        constraints={**(baseline.constraints or {}), **(constraints or {})},
        objective_weights={**(baseline.objective_weights or {}), **(weights or {})},
    )
    session.add(scenario)
    session.flush()
    return scenario


def wettest_month(session: Session, country_id: int) -> int:
    """The month with the least access across the country's lanes."""
    edges = list(session.scalars(select(Edge).where(Edge.country_id == country_id, Edge.active.is_(True))))
    if not edges:
        return 1
    totals = {month: sum(seasonality.access(edge, month) for edge in edges) for month in range(1, 13)}
    return min(totals, key=lambda m: (totals[m], m))


def _preset_cost(session: Session, baseline: Scenario) -> List[Scenario]:
    return [
        _clone(
            session,
            baseline,
            name="Cost-optimised",
            description="Minimise cost with the value of supply turned down to a quarter and equity weighted at zero, "
            "stores free to open or close. The cheapest network the model can find; read the equity band before the saving.",
            levers={"optimize_hubs": True},
            weights={"cost": 1.0, "service": 0.25, "equity": 0.0},
            tags=["study", "cost"],
        )
    ]


def _preset_cost_equity(session: Session, baseline: Scenario) -> List[Scenario]:
    return _preset_cost(session, baseline) + [
        _clone(
            session,
            baseline,
            name="Cost-optimised, 90% equity floor",
            description="The same cost minimisation, but no vulnerability band may fall below 90% of its demand.",
            levers={"optimize_hubs": True},
            constraints={"equity_floor": 0.9},
            weights={"cost": 1.0, "service": 0.25, "equity": 0.0},
            tags=["study", "cost", "equity"],
        )
    ]


def _preset_modes(session: Session, baseline: Scenario) -> List[Scenario]:
    return [
        _clone(
            session,
            baseline,
            name="Road only",
            description="Every scheduled sea, river and air service withdrawn; what road alone can reach.",
            levers={"allowed_modes": ["road"]},
            tags=["study", "modes"],
        ),
        _clone(
            session,
            baseline,
            name="Surface only (road, sea, river)",
            description="No air charter. Sea and river services carry what road cannot.",
            levers={"allowed_modes": ["road", "sea", "river"]},
            tags=["study", "modes"],
        ),
        _clone(
            session,
            baseline,
            name="All modes including air",
            description="Air allowed wherever a service exists; the model chooses when it is worth the cost.",
            levers={"allowed_modes": ["road", "sea", "river", "air"]},
            tags=["study", "modes"],
        ),
    ]


def _preset_wet_season(session: Session, baseline: Scenario) -> List[Scenario]:
    month = wettest_month(session, baseline.country_id)
    label = seasonality.MONTHS[month - 1]
    return [
        _clone(
            session,
            baseline,
            name=f"Wet season stress ({label})",
            description=f"The network as it stands, held at {label} conditions all year: the lanes that close and the "
            f"facilities that are cut off when the rains come.",
            levers={"month": month},
            tags=["study", "seasonal", "risk"],
        )
    ]


PRESETS: Dict[str, dict] = {
    "cost": {
        "label": "Baseline vs cost-optimised",
        "description": "Today's network against the cheapest one the model can find.",
        "make": _preset_cost,
    },
    "cost_equity": {
        "label": "Cost-optimised, with and without an equity floor",
        "description": "What the 90% floor costs, and who it protects.",
        "make": _preset_cost_equity,
    },
    "modes": {
        "label": "Mode study: road, surface, air",
        "description": "Three networks from one baseline: road only, road with sea and river, everything including air.",
        "make": _preset_modes,
    },
    "wet_season": {
        "label": "Wet season stress",
        "description": "The baseline held at the wettest month of the year.",
        "make": _preset_wet_season,
    },
}


def presets_out() -> List[dict]:
    return [{"key": key, "label": p["label"], "description": p["description"]} for key, p in PRESETS.items()]


# --- studies -----------------------------------------------------------------------


def baseline_for(session: Session, country_id: int) -> Optional[Scenario]:
    return session.scalar(select(Scenario).where(Scenario.country_id == country_id, Scenario.is_baseline.is_(True)))


def create(
    session: Session,
    country: Country,
    *,
    question: str,
    note: str = "",
    preset: Optional[str] = None,
    scenario_ids: Optional[List[int]] = None,
    author_claim: str,
    batch_id: str,
) -> Study:
    baseline = baseline_for(session, country.id)
    if baseline is None:
        raise ValueError("This country has no baseline scenario; load data before starting a study.")
    ids: List[int] = [baseline.id]
    for sid in scenario_ids or []:
        scenario = session.get(Scenario, sid)
        if scenario and scenario.country_id == country.id and sid not in ids:
            ids.append(sid)
    if preset:
        if preset not in PRESETS:
            raise ValueError(f"Unknown preset '{preset}'. Choose from {', '.join(PRESETS)}.")
        for scenario in PRESETS[preset]["make"](session, baseline):
            ids.append(scenario.id)
            ledger.record(
                session,
                country_id=country.id,
                entity_type="scenario",
                entity_ref=scenario.name,
                field="created",
                new_value=f"From the '{PRESETS[preset]['label']}' preset for the study “{question}”.",
                provenance="assumption",
                author_claim=author_claim,
                batch_id=batch_id,
            )
    study = Study(country_id=country.id, question=question, note=note or "", scenario_ids=ids, author_claim=author_claim)
    session.add(study)
    session.flush()
    ledger.record(
        session,
        country_id=country.id,
        entity_type="study",
        entity_ref=question,
        field="created",
        new_value=f"{len(ids)} scenarios",
        provenance="assumption",
        author_claim=author_claim,
        batch_id=batch_id,
    )
    return study


def scenarios_of(session: Session, study: Study) -> List[Scenario]:
    """In the study's order, dropping any scenario that has since been deleted."""
    found = {s.id: s for s in session.scalars(select(Scenario).where(Scenario.id.in_(study.scenario_ids or [])))}
    return [found[i] for i in (study.scenario_ids or []) if i in found]


def latest_ok(session: Session, scenario_id: int) -> Optional[Result]:
    return session.scalars(
        select(Result)
        .where(Result.scenario_id == scenario_id, Result.status == "ok")
        .order_by(Result.id.desc())
        .limit(1)
    ).first()


def run_all(session: Session, study: Study) -> List[Result]:
    return [run_scenario(session, scenario) for scenario in scenarios_of(session, study)]


# --- the compare view ---------------------------------------------------------------

LEVER_LABELS = {
    "month": "conditions",
    "allowed_modes": "modes allowed",
    "hub_nodes_open": "stores forced open",
    "hub_nodes_closed": "stores forced closed",
    "optimize_hubs": "stores free to open or close",
    "service_frequency_overrides": "service frequencies",
    "delivery_frequency_by_level": "delivery frequency by level",
    "third_party_share": "third-party share",
    "integration_policy": "integration",
    "fuel_index": "fuel index",
    "demand_growth": "demand growth",
    "safety_stock_days": "safety stock days",
    "min_fill_rate": "fill rate floor",
    "equity_floor": "equity floor",
    "max_budget": "budget ceiling",
    "respect_capacity": "respect capacity",
    "cost": "cost weight",
    "service": "service weight",
    "equity": "equity weight",
}


def _fmt(value) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k} {v}" for k, v in value.items()) or "none"
    return str(value)


def lever_diff(baseline: Scenario, scenario: Scenario) -> dict:
    """Which levers, constraints and weights differ from the baseline, and a sentence."""
    differences = []
    for group in ("levers", "constraints", "objective_weights"):
        before, after = getattr(baseline, group) or {}, getattr(scenario, group) or {}
        for key in sorted(set(before) | set(after)):
            if key == "month" and not before.get(key) and not after.get(key):
                continue
            if before.get(key) != after.get(key):
                label = LEVER_LABELS.get(key, key.replace("_", " "))
                b = seasonality.MONTHS[int(before[key]) - 1] if key == "month" and before.get(key) else _fmt(before.get(key))
                a = seasonality.MONTHS[int(after[key]) - 1] if key == "month" and after.get(key) else _fmt(after.get(key))
                if key == "month":
                    b, a = (b if before.get(key) else "annualised"), (a if after.get(key) else "annualised")
                differences.append({"group": group, "key": key, "label": label, "baseline": before.get(key), "value": after.get(key), "text": f"{label}: {b} → {a}"})
    if not differences:
        sentence = "Nothing differs from the baseline; only the data or the run conditions could."
    elif len(differences) == 1:
        sentence = f"Only one thing differs: {differences[0]['text']}."
    elif len(differences) <= 3:
        sentence = "Only " + ", ".join(d["label"] for d in differences[:-1]) + f" and {differences[-1]['label']} differ."
    else:
        sentence = f"{len(differences)} settings differ: " + ", ".join(d["label"] for d in differences[:4]) + "…"
    return {"differences": differences, "sentence": sentence}


def facilities_moved(baseline_result: Result, result: Result, limit: int = 8) -> dict:
    """The facilities that gained or lost supply against the baseline, by name."""
    before = {n["code"]: n for n in (baseline_result.per_node_detail or [])}
    gained, lost, resupplied = [], [], []
    for node in result.per_node_detail or []:
        was = before.get(node.get("code"))
        if not was:
            continue
        delta = (node.get("served_m3") or 0.0) - (was.get("served_m3") or 0.0)
        row = {
            "code": node.get("code"),
            "name": node.get("name"),
            "admin1": node.get("admin1"),
            "population": node.get("population"),
            "fill_before": was.get("fill_rate"),
            "fill_after": node.get("fill_rate"),
            "served_delta_m3": round(delta, 3),
        }
        if delta > 0.05:
            gained.append(row)
        elif delta < -0.05:
            lost.append(row)
        hub_was = (was.get("served_by") or [{}])[0].get("hub_name") if was.get("served_by") else None
        hub_now = (node.get("served_by") or [{}])[0].get("hub_name") if node.get("served_by") else None
        if hub_was and hub_now and hub_was != hub_now:
            resupplied.append({**row, "from_hub": hub_was, "to_hub": hub_now})
    gained.sort(key=lambda r: -r["served_delta_m3"])
    lost.sort(key=lambda r: r["served_delta_m3"])
    return {
        "gained": gained[:limit],
        "lost": lost[:limit],
        "gained_count": len(gained),
        "lost_count": len(lost),
        "resupplied": resupplied[:limit],
        "resupplied_count": len(resupplied),
        "people_lost": sum(int(r["population"] or 0) for r in lost if (r["fill_before"] or 0) >= 0.999 and (r["fill_after"] or 0) < 0.999),
    }


def compare(session: Session, study: Study) -> dict:
    """Everything the study view shows, from what is already stored."""
    scenarios = scenarios_of(session, study)
    baseline = next((s for s in scenarios if s.is_baseline), None)
    baseline_result = latest_ok(session, baseline.id) if baseline else None

    rows, equity, levers, moved = [], [], {}, {}
    for scenario in scenarios:
        result = latest_ok(session, scenario.id)
        row = {
            "scenario_id": scenario.id,
            "name": scenario.name,
            "description": scenario.description,
            "is_baseline": scenario.is_baseline,
            "tags": normalise_tags(scenario.tags),
            "status": result.status if result else "not_run",
            "result_id": result.id if result else None,
            "kpi_set": result.kpi_set if result else {},
            "month_label": (result.solver_log or {}).get("month_label", "Annualised") if result else None,
            "hubs_open_codes": (result.solver_log or {}).get("hubs_open_codes", []) if result else [],
            "comparison": (
                kpi_mod.compare(baseline_result.kpi_set, result.kpi_set)
                if result and baseline_result and not scenario.is_baseline
                else {}
            ),
            "confidence": confidence_mod.assess(result, baseline_result if not scenario.is_baseline else None) if result else None,
            "recommended": study.recommended_scenario_id == scenario.id,
        }
        rows.append(row)
        if result:
            equity.append(
                {
                    "scenario_id": scenario.id,
                    "name": scenario.name,
                    "strata": [
                        {"label": s.get("label"), "fill_rate": s.get("fill_rate"), "cost_per_capita": s.get("cost_per_capita"), "population": s.get("population")}
                        for s in (result.equity_detail or {}).get("strata", [])
                    ],
                }
            )
        if baseline and not scenario.is_baseline:
            levers[str(scenario.id)] = lever_diff(baseline, scenario)
            if result and baseline_result:
                moved[str(scenario.id)] = facilities_moved(baseline_result, result)

    return {
        "study": study_out(session, study),
        "baseline_scenario_id": baseline.id if baseline else None,
        "rows": rows,
        "lever_diff": levers,
        "equity": equity,
        "facilities": moved,
        "verdict": verdict(rows, study),
        "kpi_meta": kpi_mod.KPI_META,
    }


def verdict(rows: List[dict], study: Study) -> str:
    """One paragraph a reader can carry: cheapest, best for the bottom band, and what is backed."""
    ran = [r for r in rows if r["status"] == "ok" and r["kpi_set"]]
    if len(ran) < 2:
        return "Run the study's scenarios to compare them."
    options = [r for r in ran if not r["is_baseline"]] or ran
    cheapest = min(ran, key=lambda r: r["kpi_set"].get("total_cost", float("inf")))
    fairest = max(ran, key=lambda r: r["kpi_set"].get("worst_stratum_fill_rate", 0.0))
    parts = [
        f"Of {len(ran)} scenarios run, “{cheapest['name']}” is the cheapest at {cheapest['kpi_set']['total_cost']:,.0f} a year"
        + (
            f" ({cheapest['comparison']['total_cost']['delta_pct']:+.1%} against today)"
            if cheapest.get("comparison", {}).get("total_cost", {}).get("delta_pct") is not None
            else ""
        )
        + f", and “{fairest['name']}” keeps the most vulnerable fifth best supplied at {fairest['kpi_set'].get('worst_stratum_fill_rate', 0):.1%}."
    ]
    if cheapest["scenario_id"] == fairest["scenario_id"]:
        parts = [
            f"Of {len(ran)} scenarios run, “{cheapest['name']}” is both the cheapest at {cheapest['kpi_set']['total_cost']:,.0f} a year "
            f"and the one that keeps the most vulnerable fifth best supplied, at {fairest['kpi_set'].get('worst_stratum_fill_rate', 0):.1%}."
        ]
    backed = next((r for r in rows if r["recommended"]), None)
    if backed:
        conf = backed.get("confidence") or {}
        holds = conf.get("holds")
        parts.append(
            f"The analyst backs “{backed['name']}”"
            + (", and it holds with the estimates a third either way." if holds else (", though the answer depends on the estimates." if holds is False else "."))
        )
    else:
        parts.append("No option is backed yet; mark one as recommended to put it on the decision page.")
    if len(options) != len(ran):
        pass
    return " ".join(parts)


def study_out(session: Session, study: Study) -> dict:
    scenarios = scenarios_of(session, study)
    return {
        "id": study.id,
        "country_id": study.country_id,
        "question": study.question,
        "note": study.note,
        "scenario_ids": [s.id for s in scenarios],
        "scenarios": [
            {"id": s.id, "name": s.name, "is_baseline": s.is_baseline, "has_result": latest_ok(session, s.id) is not None}
            for s in scenarios
        ],
        "recommended_scenario_id": study.recommended_scenario_id,
        "author_claim": study.author_claim,
        "created_at": study.created_at,
        "updated_at": study.updated_at,
    }


# --- the diff map -------------------------------------------------------------------


def diff_map(result_a: Result, result_b: Result) -> dict:
    """One network, two answers: the lanes only A uses, only B uses, or both; and the
    facilities whose main supplier or supply changed."""
    flows_a = {f["edge_id"]: f for f in (result_a.per_edge_flow or [])}
    flows_b = {f["edge_id"]: f for f in (result_b.per_edge_flow or [])}
    lanes = []
    for edge_id in sorted(set(flows_a) | set(flows_b)):
        fa, fb = flows_a.get(edge_id), flows_b.get(edge_id)
        status = "both" if fa and fb else ("a" if fa else "b")
        f = fa or fb
        lanes.append(
            {
                "edge_id": edge_id,
                "edge_code": f.get("edge_code"),
                "hub_code": f.get("hub_code"),
                "facility_code": f.get("facility_code"),
                "mode": f.get("mode"),
                "status": status,
                "volume_a": round(fa["volume_m3"], 3) if fa else 0.0,
                "volume_b": round(fb["volume_m3"], 3) if fb else 0.0,
            }
        )
    detail_a = {n["code"]: n for n in (result_a.per_node_detail or [])}
    detail_b = {n["code"]: n for n in (result_b.per_node_detail or [])}
    facilities = []
    for code in sorted(set(detail_a) | set(detail_b)):
        na, nb = detail_a.get(code), detail_b.get(code)
        if not na or not nb:
            continue
        hub_a = (na.get("served_by") or [{}])[0].get("hub_code") if na.get("served_by") else None
        hub_b = (nb.get("served_by") or [{}])[0].get("hub_code") if nb.get("served_by") else None
        fill_a, fill_b = na.get("fill_rate") or 0.0, nb.get("fill_rate") or 0.0
        if hub_a != hub_b or abs(fill_a - fill_b) > 0.005:
            facilities.append(
                {
                    "code": code,
                    "name": na.get("name"),
                    "admin1": na.get("admin1"),
                    "population": na.get("population"),
                    "hub_a": hub_a,
                    "hub_b": hub_b,
                    "hub_a_name": (na.get("served_by") or [{}])[0].get("hub_name") if na.get("served_by") else None,
                    "hub_b_name": (nb.get("served_by") or [{}])[0].get("hub_name") if nb.get("served_by") else None,
                    "fill_a": fill_a,
                    "fill_b": fill_b,
                    "change": (
                        "gained"
                        if fill_b > fill_a + 0.005 and not hub_a
                        else "lost"
                        if fill_a > fill_b + 0.005 and not hub_b
                        else "supplier"
                        if hub_a != hub_b
                        else ("gained" if fill_b > fill_a else "lost")
                    ),
                }
            )
    only_a = sum(1 for l in lanes if l["status"] == "a")
    only_b = sum(1 for l in lanes if l["status"] == "b")
    shared = len(lanes) - only_a - only_b
    hubs_a = set((result_a.solver_log or {}).get("hubs_open_codes") or [])
    hubs_b = set((result_b.solver_log or {}).get("hubs_open_codes") or [])
    return {
        "a": {"result_id": result_a.id, "scenario_id": result_a.scenario_id, "scenario_name": result_a.scenario.name, "hubs_open": sorted(hubs_a)},
        "b": {"result_id": result_b.id, "scenario_id": result_b.scenario_id, "scenario_name": result_b.scenario.name, "hubs_open": sorted(hubs_b)},
        "lanes": lanes,
        "facilities": facilities,
        "summary": {
            "lanes_only_a": only_a,
            "lanes_only_b": only_b,
            "lanes_shared": shared,
            "facilities_changed_supplier": sum(1 for f in facilities if f["change"] == "supplier"),
            "facilities_gained": sum(1 for f in facilities if f["change"] == "gained"),
            "facilities_lost": sum(1 for f in facilities if f["change"] == "lost"),
            "hubs_only_a": sorted(hubs_a - hubs_b),
            "hubs_only_b": sorted(hubs_b - hubs_a),
        },
    }


PresetMaker = Callable[[Session, Scenario], List[Scenario]]
