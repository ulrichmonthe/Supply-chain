"""Sessions: a complete, nameable save of a country's working state.

The save-file everyone already understands, built so it can never lie about what was
saved. A session is one snapshot of every model row -- facilities, lanes, products,
demand, scenarios and their latest results, the country's settings -- gzipped and
content-addressed, plus a name, a note, who saved it and where the ledger stood.

Opening a session makes the working state exactly that snapshot, ids and all, so a
result's per-facility detail still points at the facility it was solved for. The
saved session itself is never modified again: what you open is a copy, and the
working state you were in is saved first as a draft, so nothing typed since the last
save can be lost by opening something older.

This is deliberately not the import pipeline. An import is data entering the model
from outside and has to be validated, diffed and reconciled; a session is the model
itself, and the only honest restore of a model is the model, column for column.
Serialising every mapped column generically is what keeps the two from drifting
apart: a column added to a model is in the next snapshot without anyone remembering.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, inspect, select
from sqlalchemy.orm import Session

from . import ledger
from .db import INCLUDE_RETIRED
from .models import (
    AuditEntry,
    Country,
    DatasetSnapshot,
    Demand,
    Edge,
    Node,
    Product,
    Result,
    Scenario,
    WorkSession,
)

SNAPSHOT_VERSION = 1

#: Tables in the order they can be inserted without a foreign key pointing at nothing.
_TABLES = (
    ("products", Product),
    ("nodes", Node),
    ("edges", Edge),
    ("demand", Demand),
    ("scenarios", Scenario),
    ("results", Result),
)
_COUNTRY_FIELDS = ("name", "currency", "config", "boundary")


# --- serialisation ------------------------------------------------------------------


def _columns(model) -> list:
    return [attr.key for attr in inspect(model).column_attrs]


def _datetime_columns(model) -> set:
    return {
        attr.key
        for attr in inspect(model).column_attrs
        if attr.columns and type(attr.columns[0].type).__name__ == "DateTime"
    }


def _row(obj) -> dict:
    out = {}
    for key in _columns(type(obj)):
        value = getattr(obj, key)
        out[key] = value.isoformat() if isinstance(value, datetime) else value
    return out


def _object(model, row: dict):
    stamps = _datetime_columns(model)
    values = {}
    for key in _columns(model):
        if key not in row:
            continue
        value = row[key]
        if key in stamps and isinstance(value, str):
            value = datetime.fromisoformat(value)
        values[key] = value
    return model(**values)


def _latest_results(session: Session, scenario_ids: List[int]) -> List[Result]:
    """One result per scenario -- the latest -- so a save is bounded by the scenarios,
    not by how many times the month slider was moved."""
    if not scenario_ids:
        return []
    latest = (
        select(Result.scenario_id, func.max(Result.id).label("id"))
        .where(Result.scenario_id.in_(scenario_ids))
        .group_by(Result.scenario_id)
        .subquery()
    )
    return list(session.scalars(select(Result).join(latest, Result.id == latest.c.id)))


def capture(session: Session, country: Country) -> dict:
    """The working state as a plain dict, every column of every row, retired rows too."""
    scenarios = list(
        session.scalars(select(Scenario).where(Scenario.country_id == country.id).order_by(Scenario.id))
    )
    data: Dict[str, Any] = {
        "version": SNAPSHOT_VERSION,
        "country": {key: getattr(country, key) for key in _COUNTRY_FIELDS},
        "scenarios": [_row(s) for s in scenarios],
        "results": [_row(r) for r in _latest_results(session, [s.id for s in scenarios])],
    }
    for key, model in _TABLES[:4]:
        rows = session.scalars(
            select(model).where(model.country_id == country.id).order_by(model.id).execution_options(**{INCLUDE_RETIRED: True})
        )
        data[key] = [_row(r) for r in rows]
    return data


def canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def digest(data: dict) -> str:
    return hashlib.sha256(canonical(data)).hexdigest()


def store_snapshot(session: Session, country: Country, data: dict) -> DatasetSnapshot:
    """Keep the snapshot once. Two saves of the same state share one row."""
    raw = canonical(data)
    sha = hashlib.sha256(raw).hexdigest()
    existing = session.scalars(
        select(DatasetSnapshot).where(DatasetSnapshot.country_id == country.id, DatasetSnapshot.sha256 == sha)
    ).first()
    if existing:
        return existing
    snapshot = DatasetSnapshot(country_id=country.id, sha256=sha, payload=gzip.compress(raw, 6), size_bytes=len(raw))
    session.add(snapshot)
    session.flush()
    return snapshot


def load_snapshot(snapshot: DatasetSnapshot) -> dict:
    return json.loads(gzip.decompress(snapshot.payload).decode("utf-8"))


# --- saving and opening -------------------------------------------------------------


def ledger_position(session: Session, country_id: int) -> int:
    return int(session.scalar(select(func.max(AuditEntry.id)).where(AuditEntry.country_id == country_id)) or 0)


def changes_since(session: Session, country: Country) -> int:
    """Sittings since the session was saved or opened, not ledger rows.

    One population edit writes a row for the population and one for every estimate
    that followed it; a person made one change, and that is what the shelf says.
    """
    return int(
        session.scalar(
            select(func.count(func.distinct(func.coalesce(AuditEntry.batch_id, AuditEntry.id)))).where(
                AuditEntry.country_id == country.id,
                AuditEntry.id > (country.current_session_position or 0),
                AuditEntry.status == "applied",
            )
        )
        or 0
    )


def summarise(data: dict) -> dict:
    """The counts a shelf entry shows without opening the snapshot."""
    results = {r["scenario_id"]: r for r in data.get("results", [])}
    return {
        "facilities": sum(1 for n in data.get("nodes", []) if not n.get("retired_at") and (n.get("level") or 0) >= 2),
        "stores": sum(1 for n in data.get("nodes", []) if not n.get("retired_at") and (n.get("level") or 0) < 2),
        "lanes": sum(1 for e in data.get("edges", []) if not e.get("retired_at")),
        "products": sum(1 for p in data.get("products", []) if not p.get("retired_at")),
        "demand_rows": sum(1 for d in data.get("demand", []) if not d.get("retired_at")),
        "scenarios": len(data.get("scenarios", [])),
        "results": sum(1 for r in results.values() if r.get("status") == "ok"),
        "scenario_kpis": [
            {
                "name": s["name"],
                "is_baseline": bool(s.get("is_baseline")),
                "status": (results.get(s["id"]) or {}).get("status"),
                "kpi_set": {
                    k: v
                    for k, v in ((results.get(s["id"]) or {}).get("kpi_set") or {}).items()
                    if k in ("total_cost", "fill_rate", "worst_stratum_fill_rate", "hubs_open")
                },
            }
            for s in data.get("scenarios", [])
        ],
    }


def save(
    session: Session,
    country: Country,
    *,
    name: str,
    note: str = "",
    kind: str = "saved",
    author_claim: str,
    batch_id: str,
    parent_id: Optional[int] = None,
) -> WorkSession:
    """Snapshot the working state under a name. The working state then *is* this session."""
    data = capture(session, country)
    snapshot = store_snapshot(session, country, data)
    work = WorkSession(
        country_id=country.id,
        name=name,
        note=note or "",
        kind=kind,
        parent_id=parent_id if parent_id is not None else country.current_session_id,
        snapshot_id=snapshot.id,
        ledger_position=ledger_position(session, country.id),
        summary=summarise(data),
        author_claim=author_claim,
    )
    session.add(work)
    session.flush()
    if kind == "saved":
        ledger.record(
            session,
            country_id=country.id,
            entity_type="session",
            entity_ref=name,
            field="saved",
            new_value=f"{work.summary['facilities']} facilities, {work.summary['scenarios']} scenarios, "
            f"{work.summary['results']} results",
            provenance="manual_override",
            confidence_marker="S",
            rationale=note or "",
            author_claim=author_claim,
            batch_id=batch_id,
        )
        country.current_session_id = work.id
        country.current_session_position = ledger_position(session, country.id)
        work.ledger_position = country.current_session_position
    return work


def restore(session: Session, country_id: int, data: dict) -> dict:
    """Make the working state exactly ``data``. Returns what was written."""
    country = session.get(Country, country_id)
    if country is None:
        raise ValueError(f"No country with id {country_id}.")
    scenario_ids = select(Scenario.id).where(Scenario.country_id == country_id)
    opts = {INCLUDE_RETIRED: True}
    session.execute(delete(Result).where(Result.scenario_id.in_(scenario_ids)).execution_options(**opts))
    session.execute(delete(Scenario).where(Scenario.country_id == country_id).execution_options(**opts))
    for model in (Demand, Edge, Node, Product):
        session.execute(delete(model).where(model.country_id == country_id).execution_options(**opts))
    session.flush()
    # The identity map still holds the rows just deleted; the same ids are about to
    # come back as new objects, and SQLAlchemy would rightly refuse the collision.
    session.expunge_all()
    country = session.get(Country, country_id)

    for key in _COUNTRY_FIELDS:
        if key in (data.get("country") or {}):
            setattr(country, key, data["country"][key])

    counts = {}
    for key, model in _TABLES:
        rows = data.get(key) or []
        for row in rows:
            if key != "results":
                row = {**row, "country_id": country_id}
            session.add(_object(model, row))
        counts[key] = len(rows)
        session.flush()
    return counts


def open_session(
    session: Session,
    country: Country,
    work: WorkSession,
    *,
    author_claim: str,
    batch_id: str,
) -> Tuple[dict, Optional[WorkSession]]:
    """Open a session: draft the working state if it has unsaved changes, then restore.

    Returns the restore counts and the draft, if one was made.
    """
    draft = None
    current_data = capture(session, country)
    unsaved = country.current_session_id is None or changes_since(session, country) > 0
    if unsaved and digest(current_data) != work.snapshot.sha256:
        current = session.get(WorkSession, country.current_session_id) if country.current_session_id else None
        if current is None:
            draft_name = f"Draft before opening “{work.name}”"
        elif current.id == work.id:
            draft_name = f"Draft before reopening “{work.name}”"
        else:
            draft_name = f"Draft of “{current.name}” before opening “{work.name}”"
        draft = save(
            session,
            country,
            name=draft_name,
            kind="draft",
            author_claim=author_claim,
            batch_id=batch_id,
        )
    data = load_snapshot(work.snapshot)
    draft_id = draft.id if draft else None
    counts = restore(session, country.id, data)
    # The restore cleared the identity map; take the objects up again from the database.
    country = session.get(Country, country.id)
    work = session.get(WorkSession, work.id)
    draft = session.get(WorkSession, draft_id) if draft_id else None
    ledger.record(
        session,
        country_id=country.id,
        entity_type="session",
        entity_ref=work.name,
        field="opened",
        new_value=f"{counts['nodes']} facilities, {counts['edges']} lanes, {counts['demand']} demand rows, "
        f"{counts['scenarios']} scenarios and {counts['results']} results restored",
        provenance="manual_override",
        confidence_marker="S",
        rationale=(f"The working state before this was kept as draft #{draft.id}." if draft else ""),
        author_claim=author_claim,
        batch_id=batch_id,
    )
    country.current_session_id = work.id
    country.current_session_position = ledger_position(session, country.id)
    return counts, draft


def delete_session(session: Session, work: WorkSession) -> None:
    """Remove a session, and its snapshot if nothing else points at it."""
    country = session.get(Country, work.country_id)
    if country and country.current_session_id == work.id:
        country.current_session_id = None
    snapshot_id = work.snapshot_id
    session.delete(work)
    session.flush()
    others = session.scalar(select(func.count(WorkSession.id)).where(WorkSession.snapshot_id == snapshot_id))
    if not others:
        session.execute(delete(DatasetSnapshot).where(DatasetSnapshot.id == snapshot_id))


# --- the readable diff --------------------------------------------------------------


def _by(rows: List[dict], *keys: str) -> Dict[tuple, dict]:
    return {tuple(row.get(k) for k in keys): row for row in rows}


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "blank"
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, dict):
        return "{…}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "none"
    return str(value)


def _changed_fields(a: dict, b: dict, ignore=("id", "country_id", "last_import", "retired_at", "retired_reason")) -> list:
    return [k for k in b if k not in ignore and a.get(k) != b.get(k)]


def diff(a: dict, b: dict) -> dict:
    """What changed from snapshot ``a`` to snapshot ``b``, counted and said in words."""
    sentences: List[str] = []
    detail: Dict[str, Any] = {}

    # Facilities.
    an, bn = _by(a.get("nodes", []), "code"), _by(b.get("nodes", []), "code")
    live_a = {k for k, v in an.items() if not v.get("retired_at")}
    live_b = {k for k, v in bn.items() if not v.get("retired_at")}
    added = sorted(live_b - live_a)
    gone = sorted(live_a - live_b)
    moved, population, storage, other = [], [], [], []
    for code in sorted(live_a & live_b):
        fields = _changed_fields(an[code], bn[code])
        if "lat" in fields or "lon" in fields:
            moved.append(code)
        if "catchment_population" in fields:
            population.append(code)
        if "capacity" in fields:
            storage.append(code)
        rest = [f for f in fields if f not in ("lat", "lon", "catchment_population", "capacity", "derivations")]
        if rest:
            other.append((code, rest))
    if added:
        sentences.append(f"{_plural(len(added), 'facility', 'facilities')} added: {', '.join(bn[c]['name'] for c in added[:4])}{'…' if len(added) > 4 else ''}.")
    if gone:
        sentences.append(f"{_plural(len(gone), 'facility', 'facilities')} retired: {', '.join(an[c]['name'] for c in gone[:4])}{'…' if len(gone) > 4 else ''}.")
    if moved:
        sentences.append(f"{_plural(len(moved), 'facility', 'facilities')} re-geocoded.")
    if population:
        sentences.append(f"Population changed at {_plural(len(population), 'facility', 'facilities')}.")
    if storage:
        sentences.append(f"Storage changed at {_plural(len(storage), 'facility', 'facilities')}.")
    if other:
        fields = sorted({f for _, fs in other for f in fs})
        sentences.append(
            f"Other details changed at {_plural(len(other), 'facility', 'facilities')} ({', '.join(fields[:5])}{'…' if len(fields) > 5 else ''})."
        )
    detail["facilities"] = {"added": added, "retired": gone, "moved": moved, "population": population, "storage": storage}

    # Demand.
    node_name = {**{k[0]: v["name"] for k, v in an.items()}, **{k[0]: v["name"] for k, v in bn.items()}}
    node_code_a = {n["id"]: n["code"] for n in a.get("nodes", [])}
    node_code_b = {n["id"]: n["code"] for n in b.get("nodes", [])}
    sku_a = {p["id"]: p["sku"] for p in a.get("products", [])}
    sku_b = {p["id"]: p["sku"] for p in b.get("products", [])}
    ad = {(node_code_a.get(d["node_id"]), sku_a.get(d["product_id"])): d for d in a.get("demand", []) if not d.get("retired_at")}
    bd = {(node_code_b.get(d["node_id"]), sku_b.get(d["product_id"])): d for d in b.get("demand", []) if not d.get("retired_at")}
    estimated, typed, recomputed = set(), set(), set()
    for key, row in bd.items():
        before = ad.get(key)
        if before is None or before.get("quantity") != row.get("quantity") or bool(before.get("derivation")) != bool(row.get("derivation")):
            if row.get("derivation") and not (before or {}).get("derivation"):
                estimated.add(key[0])
            elif row.get("derivation"):
                recomputed.add(key[0])
            elif before is not None and (before.get("quantity") != row.get("quantity") or before.get("derivation")):
                typed.add(key[0])
            elif before is None and row.get("quantity"):
                typed.add(key[0])
    if estimated:
        sentences.append(f"Demand estimated for {_plural(len(estimated), 'facility', 'facilities')}.")
    if typed:
        sentences.append(f"Demand typed or imported at {_plural(len(typed), 'facility', 'facilities')}.")
    if recomputed:
        sentences.append(f"Estimates moved with their inputs at {_plural(len(recomputed), 'facility', 'facilities')}.")
    detail["demand"] = {"estimated": sorted(estimated), "typed": sorted(typed), "recomputed": sorted(recomputed)}

    # Lanes and products.
    ae, be = _by(a.get("edges", []), "code"), _by(b.get("edges", []), "code")
    lanes_a = {k for k, v in ae.items() if not v.get("retired_at")}
    lanes_b = {k for k, v in be.items() if not v.get("retired_at")}
    lanes_changed = [k for k in lanes_a & lanes_b if _changed_fields(ae[k], be[k])]
    parts = []
    if lanes_b - lanes_a:
        parts.append(f"{len(lanes_b - lanes_a)} added")
    if lanes_a - lanes_b:
        parts.append(f"{len(lanes_a - lanes_b)} retired")
    if lanes_changed:
        parts.append(f"{len(lanes_changed)} changed")
    if parts:
        sentences.append(f"Lanes: {', '.join(parts)}.")
    ap, bp = _by(a.get("products", []), "sku"), _by(b.get("products", []), "sku")
    products_changed = [k for k in ap.keys() & bp.keys() if _changed_fields(ap[k], bp[k])]
    if set(bp) - set(ap) or set(ap) - set(bp) or products_changed:
        sentences.append(
            f"Products: {len(set(bp) - set(ap))} added, {len(set(ap) - set(bp))} removed, {len(products_changed)} changed."
        )
    detail["lanes"] = {"added": sorted(lanes_b - lanes_a), "retired": sorted(lanes_a - lanes_b), "changed": sorted(lanes_changed)}

    # Scenarios: levers, constraints, weights, by name.
    a_s = {s["name"]: s for s in a.get("scenarios", [])}
    b_s = {s["name"]: s for s in b.get("scenarios", [])}
    scenario_lines: List[str] = []
    for name in b_s.keys() - a_s.keys():
        scenario_lines.append(f"Scenario “{name}” added.")
    for name in a_s.keys() - b_s.keys():
        scenario_lines.append(f"Scenario “{name}” removed.")
    for name in sorted(a_s.keys() & b_s.keys()):
        for group in ("levers", "constraints", "objective_weights"):
            before, after = a_s[name].get(group) or {}, b_s[name].get(group) or {}
            for key in sorted(set(before) | set(after)):
                if before.get(key) != after.get(key):
                    scenario_lines.append(f"{name}: {key.replace('_', ' ')} {_fmt(before.get(key))} → {_fmt(after.get(key))}.")
        if sorted(a_s[name].get("tags") or []) != sorted(b_s[name].get("tags") or []):
            scenario_lines.append(f"{name}: tags {_fmt(a_s[name].get('tags'))} → {_fmt(b_s[name].get('tags'))}.")
    sentences.extend(scenario_lines[:12])
    if len(scenario_lines) > 12:
        sentences.append(f"…and {len(scenario_lines) - 12} more scenario changes.")
    detail["scenarios"] = scenario_lines

    # Results: the headline cost, where both sides have one.
    a_r = {r["scenario_id"]: r for r in a.get("results", [])}
    b_r = {r["scenario_id"]: r for r in b.get("results", [])}
    result_lines: List[str] = []
    for name in sorted(a_s.keys() & b_s.keys()):
        ra, rb = a_r.get(a_s[name]["id"]), b_r.get(b_s[name]["id"])
        if not ra or not rb or ra.get("status") != "ok" or rb.get("status") != "ok":
            continue
        ca, cb = (ra.get("kpi_set") or {}).get("total_cost"), (rb.get("kpi_set") or {}).get("total_cost")
        fa, fb = (ra.get("kpi_set") or {}).get("fill_rate"), (rb.get("kpi_set") or {}).get("fill_rate")
        if ca is not None and cb is not None and abs(cb - ca) > 0.5:
            result_lines.append(f"{name}: annual cost {ca:,.0f} → {cb:,.0f}" + (f", fill {fa:.1%} → {fb:.1%}" if fa != fb and fa is not None else "") + ".")
    sentences.extend(result_lines[:8])
    detail["results"] = result_lines

    # Settings.
    ca, cb = (a.get("country") or {}).get("config") or {}, (b.get("country") or {}).get("config") or {}
    settings = sorted(k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k))
    if settings:
        sentences.append(f"Settings changed: {', '.join(settings[:6])}{'…' if len(settings) > 6 else ''}.")

    if not sentences:
        sentences.append("Nothing differs. The two are the same working state.")
    return {"sentences": sentences, "same": digest(a) == digest(b), "detail": detail}
