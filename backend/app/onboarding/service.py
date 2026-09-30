"""The pipeline, step by step: a run, its files, their profiles and mappings, the staged
records with conflicts kept, the facilities reconciled into the crosswalk, the rules
run, the estimates proposed. Deterministic: the same files, mappings and pack produce
the same staging, with no model call anywhere on this path.

A refresh run replays what the last run taught it -- saved mappings, the crosswalk, the
accepted rules -- and marks every value that equals the approved dataset as unchanged,
so only what moved reaches a person.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..io import mapper
from ..io.schema_spec import normalise_header
from ..models import Country, Crosswalk, Demand, FacilityMatch, Node, OnboardingRun, Product, ReviewItem, SourceFile, StagedRecord
from . import DOMAINS, adapters, estimate as estimating, pack as packs, reconcile, rules
from . import profile as profiling
from .agent import proposer as choose_proposer
from .staging import add_alternative, entry, missing, value_of

#: Field -> the unit the model wants, for the conversions the pack supplies.
MODEL_UNITS = {
    "dry_m3": "m3", "cold_2_8_m3": "m3", "cold_minus20_m3": "m3", "cold_minus70_m3": "m3", "hub_throughput_m3": "m3",
    "distance_km": "km", "base_travel_time_hr": "hours", "capacity_per_trip_m3": "m3", "cold_capacity_per_trip_m3": "m3",
    "volume_per_unit_cm3": "cm3",
}
MONEY_FIELDS = {"unit_cost", "fixed_cost_per_trip", "variable_cost_per_km", "cost_per_m3", "hub_fixed_cost", "hub_open_capex"}
NUMERIC_FIELDS = set(MODEL_UNITS) | MONEY_FIELDS | {"lat", "lon", "catchment_population", "quantity", "geocode_confidence", "level", "reliability", "lead_time_sd_days", "shelf_life_days", "confidence", "period"}


class OnboardingError(ValueError):
    """A step refused, with a message for the person at the screen."""


# --- runs ------------------------------------------------------------------------------


def create_run(session: Session, country: Country, *, name: str, preparer: str, kind: str = "initial") -> OnboardingRun:
    pack = packs.ensure(session, country)
    if pack.status != "approved":
        raise OnboardingError("The country pack is not approved yet. Ingestion starts only from an approved pack.")
    check = packs.completeness(packs.validate(pack.pack))
    if not check["complete"]:
        needs = ", ".join(m["needs"] for m in check["missing"])
        raise OnboardingError(f"The country pack is not complete: it still needs {needs}.")
    if kind not in ("initial", "refresh"):
        raise OnboardingError("A run is initial or a refresh.")
    run = OnboardingRun(country_id=country.id, pack_id=pack.id, name=name.strip() or f"Onboarding {datetime.now(timezone.utc):%Y-%m-%d}", kind=kind, preparer=preparer, summary={})
    session.add(run)
    session.flush()
    return run


def run_pack(session: Session, run: OnboardingRun) -> packs.Pack:
    pack = session.get(packs.CountryPack, run.pack_id) if run.pack_id else None
    if pack is None:
        pack = packs.ensure(session, session.get(Country, run.country_id))
    return packs.validate(pack.pack)


def _open(run: OnboardingRun) -> None:
    if run.status not in ("open", "in_review"):
        raise OnboardingError(f"This run is {run.status.replace('_', ' ')}; it no longer accepts changes.")


# --- files -----------------------------------------------------------------------------


def add_file(session: Session, run: OnboardingRun, *, filename: str, data: bytes, uploader: str, source_system: str = "", domain: str = "", vintage_from: str = "", vintage_to: str = "") -> SourceFile:
    """FR1-FR3: record the file, profile it, and keep the bytes so staging is reproducible."""
    _open(run)
    country = session.get(Country, run.country_id)
    prof = profiling.profile(filename, data, saved_mappings=mapper.saved_mappings(country))
    duplicate = session.scalar(select(SourceFile).where(SourceFile.run_id == run.id, SourceFile.sha256 == prof["sha256"]))
    if duplicate:
        raise OnboardingError(f"This file is already in the run as {duplicate.filename} (same checksum).")
    source = SourceFile(
        run_id=run.id, filename=filename, sha256=prof["sha256"], size_bytes=len(data), uploader=uploader,
        source_system=source_system or prof["system"], domain=domain or prof["domain"],
        vintage_from=vintage_from or prof["vintage_from"], vintage_to=vintage_to or prof["vintage_to"],
        profile=prof, mapping={}, status="profiled", payload=data,
    )
    session.add(source)
    session.flush()
    _mapping_item(session, run, source)
    return source


def _mapping_item(session: Session, run: OnboardingRun, source: SourceFile) -> None:
    """One review item per file: confirm the proposed mapping. The proposals come from
    the rules or, where the pack allows it, the agent -- and say which."""
    prof = source.profile or {}
    sheet = next((s for s in prof.get("sheets", []) if s["name"] == prof.get("primary_sheet")), None)
    if not sheet:
        return
    pack = run_pack(session, run)
    who = choose_proposer(pack)
    proposals = who.propose_mappings(source.domain or sheet["domain"], sheet["columns"], sheet.get("sample") or [])
    adapter = adapters.choose(source.source_system, source.domain or sheet["domain"], sheet["columns"])
    session.add(ReviewItem(
        run_id=run.id, kind="mapping", subject=f"file:{source.id}", title=f"Confirm how {source.filename} maps to our columns",
        detail=(f"Recognised as a {source.source_system} export; no manual mapping needed." if adapter != "generic" else f"{len(proposals)} of {len(sheet['columns'])} columns matched by {who.name}."),
        confidence="high" if adapter != "generic" or all(p["confidence"] == "high" for p in proposals) else "medium",
        impact=float(sheet.get("row_count") or 0), proposed_by=who.name,
        payload={"adapter": adapter, "proposals": proposals, "sheet": sheet["name"], "domain": source.domain or sheet["domain"], "matched_saved": sheet.get("matched_saved")},
    ))


def set_mapping(session: Session, run: OnboardingRun, source: SourceFile, *, mapping: Dict[str, Optional[str]], sheet: Optional[str] = None, aux_columns: Optional[List[str]] = None, domain: Optional[str] = None, units: Optional[Dict[str, str]] = None, save_as: str = "", by: str = "anonymous") -> SourceFile:
    """FR13: a confirmed mapping, kept per country and source system for next time."""
    _open(run)
    prof = source.profile or {}
    sheet_name = sheet or prof.get("primary_sheet")
    if domain:
        source.domain = domain
    clean = {k: v for k, v in mapping.items() if v}
    source.mapping = {"sheet": sheet_name, "mapping": clean, "aux_columns": aux_columns or [], "units": units or {}, "confirmed_by": by, "confirmed_at": datetime.now(timezone.utc).isoformat()}
    source.status = "mapped"
    if save_as:
        country = session.get(Country, run.country_id)
        mapper.save_mapping(country, name=save_as, sheet=source.domain, mapping=clean, columns=prof.get("sheets", [{}])[0].get("columns", []), saved_at=datetime.now(timezone.utc).isoformat())
    for item in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.kind == "mapping", ReviewItem.subject == f"file:{source.id}", ReviewItem.decision == "pending")):
        item.decision, item.decided_by, item.decided_at, item.choice = "accepted", by, datetime.now(timezone.utc), "mapping confirmed"
    session.flush()
    return source


# --- staging -------------------------------------------------------------------------


def stage_file(session: Session, run: OnboardingRun, source: SourceFile, *, by: str = "anonymous") -> dict:
    """Turn a mapped file into staged records, reconciling facilities on the way."""
    _open(run)
    pack = run_pack(session, run)
    prof = source.profile or {}
    sheets = profiling.read_sheets(source.filename, source.payload)
    conf = source.mapping or {}
    sheet_name = conf.get("sheet") or prof.get("primary_sheet")
    if sheet_name not in sheets:
        raise OnboardingError(f"Sheet {sheet_name!r} is not in {source.filename}.")
    columns, rows = sheets[sheet_name]
    domain = source.domain or prof.get("domain") or "Nodes"
    if domain not in DOMAINS:
        raise OnboardingError(f"The file's domain must be one of {DOMAINS}.")
    adapter = adapters.choose(source.source_system, domain, columns)
    mapping = conf.get("mapping") or {}
    if adapter == "generic":
        if not mapping:
            raise OnboardingError("Confirm the column mapping before staging this file.")
        missing_required = mapper.missing_required(domain, mapping)
        if missing_required:
            raise OnboardingError(f"The mapping still needs {', '.join(missing_required)} before this file can be staged.")
    raw_rows = adapters.run(adapter, sheet_name, columns, rows, mapping, conf.get("aux_columns") or [])
    units = {**{normalise_header(k): v for k, v in (prof.get("sheets", [{}])[0].get("units") or {}).items()}, **{normalise_header(k): v for k, v in (conf.get("units") or {}).items()}}
    column_units = {field: units.get(normalise_header(column)) for field, column in mapping.items() if column}

    # Clear what this file staged before, so re-staging is idempotent.
    for record in session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id)):
        _forget_file(record, source.id)
    for old in session.scalars(select(FacilityMatch).where(FacilityMatch.run_id == run.id, FacilityMatch.source_file_id == source.id)):
        session.delete(old)
    for old in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.kind.in_(("match", "conflict", "unit")), ReviewItem.subject.like(f"%file:{source.id}%"))):
        session.delete(old)
    session.flush()

    counts = {"rows": len(raw_rows), "staged": 0, "conflicts": 0, "matched": 0, "auto_accepted": 0, "pending": 0, "new": 0, "unresolved": 0, "converted": 0}
    vintage = f"{source.vintage_from}..{source.vintage_to}".strip(".") if source.vintage_from or source.vintage_to else ""
    common = dict(source_file_id=source.id, system=source.source_system or "file", vintage=vintage)
    previous = _previous_values(session, run) if run.kind == "refresh" else {}

    if domain == "Nodes":
        counts.update(_stage_nodes(session, run, source, raw_rows, pack, common, column_units, by, previous))
    else:
        counts.update(_stage_rows(session, run, source, domain, raw_rows, pack, common, column_units, previous))
    source.status = "staged"
    if adapter != "generic":
        # A recognised export needs no mapping decision; the adapter is the record of how.
        for item in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.kind == "mapping", ReviewItem.subject == f"file:{source.id}", ReviewItem.decision == "pending")):
            item.decision, item.decided_by, item.decided_at, item.choice = "accepted", "pipeline", datetime.now(timezone.utc), f"adapter:{adapter}"
    session.flush()
    return counts


def _forget_file(record: StagedRecord, file_id: int) -> None:
    # JSON columns only notice a new object, never a dict mutated in place: copy deep, then write back.
    fields = copy.deepcopy(record.fields or {})
    changed = False
    for name, item in list(fields.items()):
        alternatives = [a for a in (item.get("alternatives") or []) if a.get("source_file_id") != file_id]
        if len(alternatives) != len(item.get("alternatives") or []):
            item = dict(item, alternatives=alternatives or None)
            changed = True
        if item.get("source_file_id") == file_id:
            if alternatives:
                nxt = dict(alternatives[0])
                nxt["alternatives"] = alternatives[1:] or None
                fields[name] = nxt
            else:
                fields.pop(name)
            changed = True
        elif changed:
            fields[name] = item
    if changed:
        record.fields = fields
        if not fields:
            record.status = "blocked"


def _convert(field: str, value, unit: Optional[str], pack: packs.Pack, common: dict, location: str) -> Tuple[dict, bool]:
    """A value into a field entry, converted to the model's unit when the pack knows how.
    A unit the pack cannot convert is a question, not a guess."""
    target = MODEL_UNITS.get(field)
    if field in MONEY_FIELDS and unit and unit.upper() != pack.currency.code.upper():
        factor = (pack.currency.fx_to_model or {}).get(unit.upper())
        if factor is None:
            return entry(value, cls="observed", unit=unit, confidence="low", reason=f"in {unit}; the pack has no rate into {pack.currency.code}", location=location, **common), False
        converted = round(float(value) * factor, 4)
        return entry(converted, cls="converted", unit=pack.currency.code, transformation=f"{value} {unit} × {factor} = {converted} {pack.currency.code}", confidence="medium", location=location, **common), True
    if target and unit and unit.lower() != target.lower():
        factor = packs.conversion_factor(pack, unit, target)
        if factor is None:
            return entry(value, cls="observed", unit=unit, confidence="low", reason=f"in {unit}; the pack has no conversion into {target}", location=location, **common), False
        try:
            converted = round(float(value) * factor, 6)
        except (TypeError, ValueError):
            return entry(value, cls="observed", unit=unit, confidence="low", reason="not a number", location=location, **common), False
        return entry(converted, cls="converted", unit=target, transformation=f"{value} {unit} × {factor:g} = {converted:g} {target}", confidence="high", location=location, **common), True
    if field in NUMERIC_FIELDS:
        try:
            value = float(str(value).replace(",", "")) if not isinstance(value, (int, float)) else float(value)
            if field in ("level", "period", "shelf_life_days"):
                value = int(value)
        except (TypeError, ValueError):
            return entry(value, cls="observed", unit=unit or target or "", confidence="low", reason="not a number", location=location, **common), False
    return entry(value, cls="observed", unit=unit or target or "", confidence="high", location=location, **common), False


def _merge(session: Session, run: OnboardingRun, domain: str, key: str, label: str, incoming: Dict[str, dict], aux: dict, previous: dict) -> Tuple[StagedRecord, int]:
    record = session.scalar(select(StagedRecord).where(StagedRecord.run_id == run.id, StagedRecord.domain == domain, StagedRecord.key == key))
    if record is None:
        record = StagedRecord(run_id=run.id, domain=domain, key=key, label=label or key, fields={}, aux={}, issues=[], status="staged")
        session.add(record)
        # Flushed at once so a second row for the same facility in the same file finds
        # this one and becomes an agreement or a conflict, never a duplicate.
        session.flush()
    fields = copy.deepcopy(record.fields or {})
    conflicts = 0
    for name, item in incoming.items():
        before = previous.get((domain, key, name))
        if before is not None and _same(before, item.get("value")):
            item = dict(item, unchanged=True, reason=(item.get("reason") + " " if item.get("reason") else "") + "Same as the approved dataset.")
        add_alternative(fields, name, item)
        if fields[name].get("alternatives"):
            conflicts += 1
    record.fields = fields
    record.aux = {**(record.aux or {}), **aux}
    if label and not record.label:
        record.label = label
    record.status = "conflict" if any(i.get("alternatives") for i in fields.values()) else "staged"
    return record, conflicts


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _stage_rows(session, run, source, domain, raw_rows, pack, common, column_units, previous) -> dict:
    counts = {"staged": 0, "conflicts": 0, "unresolved": 0, "converted": 0}
    country = session.get(Country, run.country_id)
    products = {p.sku: p for p in session.scalars(select(Product).where(Product.country_id == country.id))}
    aliases = {k.strip().lower(): v for k, v in (pack.products_units.product_aliases or {}).items()}
    staged_skus = {r.key for r in session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id, StagedRecord.domain == "Products"))}
    for raw in raw_rows:
        fields, locations, aux = raw["fields"], raw["locations"], raw["aux"]
        incoming: Dict[str, dict] = {}
        if domain == "Demand":
            node = _resolve_facility(session, run, str(fields.get("node") or "").strip())
            sku = _resolve_product(str(fields.get("product") or "").strip(), aliases, products, staged_skus)
            period = fields.get("period")
            if period in (None, "") and fields.get("period_code"):
                period = adapters.period_to_int(str(fields["period_code"]))
            try:
                period = int(float(period)) if period not in (None, "") else 0
            except (TypeError, ValueError):
                period = 0
            if node is None or sku is None:
                counts["unresolved"] += 1
                key = f"?{fields.get('node')}|{fields.get('product')}|{period}"
                record, _ = _merge(session, run, domain, key, f"{fields.get('node')} / {fields.get('product')}", {}, aux, previous)
                record.status = "blocked"
                record.issues = [{"code": "unresolved_reference", "severity": "error", "message": (f"No facility matches {fields.get('node')!r}" if node is None else f"No product matches {fields.get('product')!r}") + f" in {source.filename} row {raw['row']}.", "suggestion": "Reconcile the facility list first, or add the product alias to the country pack.", "field": "node" if node is None else "product"}]
                continue
            key = f"{node}|{sku}|{period}"
            label = f"{node} / {sku}" + (f" / month {period}" if period else "")
            for name in ("quantity",):
                if fields.get(name) in (None, ""):
                    continue
                item, converted = _convert(name, fields[name], column_units.get(name), pack, common, locations.get(name, ""))
                incoming[name] = item
                counts["converted"] += int(converted)
            if fields.get("source"):
                incoming["source"] = entry(str(fields["source"]).strip(), cls="observed", location=locations.get("source", ""), **common)
            if fields.get("confidence") not in (None, ""):
                incoming["confidence"] = entry(fields["confidence"], cls="observed", location=locations.get("confidence", ""), **common)
        else:
            key_field = "code" if domain == "Edges" else "sku"
            key = str(fields.get(key_field) or "").strip()
            if not key:
                counts["unresolved"] += 1
                continue
            label = str(fields.get("name") or fields.get("service_name") or key)
            for name, value in fields.items():
                if value in (None, ""):
                    continue
                item, converted = _convert(name, value, column_units.get(name), pack, common, locations.get(name, ""))
                incoming[name] = item
                counts["converted"] += int(converted)
            if domain == "Edges":
                for endpoint in ("from_node", "to_node"):
                    if endpoint in incoming:
                        resolved = _resolve_facility(session, run, str(incoming[endpoint]["value"]))
                        if resolved:
                            incoming[endpoint]["value"] = resolved
        record, conflicts = _merge(session, run, domain, key, label, incoming, aux, previous)
        counts["staged"] += 1
        counts["conflicts"] += conflicts
    session.flush()
    _conflict_items(session, run, domain)
    return counts


def _resolve_product(text: str, aliases: dict, products: dict, staged: set) -> Optional[str]:
    if not text:
        return None
    if text in products or text in staged:
        return text
    alias = aliases.get(text.lower())
    if alias:
        return alias
    for sku, product in products.items():
        if text.lower() == product.name.lower():
            return sku
    return None


def _resolve_facility(session: Session, run: OnboardingRun, text: str) -> Optional[str]:
    """A source facility identifier into a canonical code: the crosswalk first (any id it
    carries), then the model's own codes."""
    if not text:
        return None
    country_id = run.country_id
    for row in session.scalars(select(Crosswalk).where(Crosswalk.country_id == country_id)):
        if row.canonical_code == text or text in {str(v) for v in (row.ids or {}).values()}:
            return row.canonical_code
    node = session.scalar(select(Node).where(Node.country_id == country_id, Node.code == text))
    if node:
        return node.code
    for node in session.scalars(select(Node).where(Node.country_id == country_id)):
        if text in {str(v) for v in (node.external_ids or {}).values()}:
            return node.code
    # A facility this run accepted from another list.
    match = session.scalar(select(FacilityMatch).where(FacilityMatch.run_id == run.id, FacilityMatch.source_key == text, FacilityMatch.decision.in_(("auto_accepted", "accepted", "new"))))
    return match.canonical_code if match and match.canonical_code else None


# --- facilities -----------------------------------------------------------------------------


def _record_from_raw(fields: dict) -> dict:
    record = {k: v for k, v in fields.items() if v not in (None, "")}
    record["external_ids"] = {k: str(record.pop(k)) for k in ("dhis2_uid", "msupply_id", "openlmis_code", "mfl_code") if k in record}
    return record


def _targets(session: Session, run: OnboardingRun, pack: packs.Pack, exclude_file: int) -> List[reconcile.Target]:
    country_id = run.country_id
    targets = reconcile.targets_from_nodes(session.scalars(select(Node).where(Node.country_id == country_id)))
    seen = {t.code for t in targets}
    for row in session.scalars(select(Crosswalk).where(Crosswalk.country_id == country_id)):
        if row.canonical_code not in seen:
            targets.append(reconcile.targets_from_crosswalk([row])[0])
            seen.add(row.canonical_code)
    for match in session.scalars(select(FacilityMatch).where(FacilityMatch.run_id == run.id, FacilityMatch.decision == "new", FacilityMatch.source_file_id != exclude_file)):
        if match.canonical_code and match.canonical_code not in seen:
            targets.append(reconcile.target_from_record({**match.source_record, "code": match.canonical_code}, f"list:{match.source_file_id}"))
            seen.add(match.canonical_code)
    return targets


def _stage_nodes(session, run, source, raw_rows, pack, common, column_units, by, previous) -> dict:
    counts = {"staged": 0, "conflicts": 0, "matched": 0, "auto_accepted": 0, "pending": 0, "new": 0, "converted": 0}
    thresholds = pack.thresholds
    authority = pack.facility_authority
    authoritative = bool(authority.authoritative_list and authority.authoritative_list.strip().lower() in (source.filename.lower(), source.source_system.lower()))
    targets = _targets(session, run, pack, source.id)
    claimed: Dict[str, str] = {}
    for raw in raw_rows:
        record = _record_from_raw(raw["fields"])
        code = str(record.get("code") or "").strip()
        name = str(record.get("name") or "").strip()
        if not code and not name:
            continue
        outcome = reconcile.match(record, targets, auto_accept=thresholds.auto_accept_match, review_floor=thresholds.review_match_floor, geolocation_allowed=authority.geolocation_allowed)
        decision = outcome.decision
        canonical = ""
        if decision == "auto_accepted":
            canonical = outcome.best.code
            if canonical in claimed:
                decision = "pending"
                outcome.reason = f"Two rows in this file both match {canonical} ({claimed[canonical]} and {code}); one of them is something else."
            else:
                claimed[canonical] = code
        if decision == "new":
            canonical = code or _slug(name)
            if canonical in claimed or any(t.code == canonical for t in targets):
                canonical = f"{canonical}-{source.id}"
            claimed[canonical] = code
        match = FacilityMatch(run_id=run.id, source_file_id=source.id, source_key=code or name, source_name=name, source_record=record, candidates=[c.as_dict() for c in outcome.candidates], canonical_code=canonical, confidence=outcome.confidence, reason=outcome.reason, decision=decision, decided_by="pipeline" if decision != "pending" else "", decided_at=datetime.now(timezone.utc) if decision != "pending" else None)
        session.add(match)
        counts["matched" if decision == "auto_accepted" else decision] += 1
        if decision == "auto_accepted":
            counts["auto_accepted"] += 1
        if decision == "pending":
            session.flush()
            session.add(ReviewItem(
                run_id=run.id, kind="match", subject=f"match:{match.id} file:{source.id}", title=f"Which facility is {name or code}?",
                detail=outcome.reason, confidence="medium" if outcome.confidence >= 0.7 else "low",
                impact=float(record.get("catchment_population") or 0) or 1.0,
                payload={"match_id": match.id, "record": record, "candidates": [c.as_dict() for c in outcome.candidates], "arbiter": authority.arbiter},
                options=[{"code": c.code, "label": f"{c.name} ({c.source}, {c.score:.2f})"} for c in outcome.candidates] + [{"code": "__new__", "label": "A facility only this source knows"}],
            ))
            continue
        _accept_into_crosswalk(session, run, match, record, source, by="pipeline", authoritative=authoritative)
        staged = _stage_node_record(session, run, source, canonical, record, raw, pack, common, column_units, previous, authoritative, counts)
        counts["staged"] += 1
        counts["conflicts"] += staged
    session.flush()
    _conflict_items(session, run, "Nodes")
    return counts


def _slug(name: str) -> str:
    return "NEW-" + re.sub(r"[^A-Z0-9]+", "-", (name or "facility").upper()).strip("-")[:40]


def _stage_node_record(session, run, source, canonical, record, raw, pack, common, column_units, previous, authoritative, counts: Optional[dict] = None) -> int:
    incoming: Dict[str, dict] = {}
    locations = raw["locations"]
    for name, value in raw["fields"].items():
        if value in (None, "") or name == "code":
            continue
        item, converted = _convert(name, value, column_units.get(name), pack, common, locations.get(name, ""))
        if converted and counts is not None:
            counts["converted"] = counts.get("converted", 0) + 1
        if authoritative:
            item["confidence"] = "high"
            item["reason"] = (item.get("reason") + " " if item.get("reason") else "") + "From the authoritative list named in the country pack."
        incoming[name] = item
    record_row, conflicts = _merge(session, run, "Nodes", canonical, str(record.get("name") or canonical), incoming, raw["aux"], previous)
    return conflicts


def _accept_into_crosswalk(session: Session, run: OnboardingRun, match: FacilityMatch, record: dict, source: SourceFile, *, by: str, authoritative: bool = False) -> Crosswalk:
    """FR9, FR11, FR12: one canonical facility, every id it carries, and its history."""
    now = datetime.now(timezone.utc).isoformat()
    label = source.source_system if source.source_system not in ("", "unknown", "excel", "csv") else source.filename
    row = session.scalar(select(Crosswalk).where(Crosswalk.country_id == run.country_id, Crosswalk.canonical_code == match.canonical_code))
    if row is None:
        node = session.scalar(select(Node).where(Node.country_id == run.country_id, Node.code == match.canonical_code))
        row = Crosswalk(country_id=run.country_id, canonical_code=match.canonical_code, name=node.name if node else str(record.get("name") or ""), type=(node.type if node else str(record.get("type") or "")), admin1=node.admin1 if node else record.get("admin1"), admin2=node.admin2 if node else record.get("admin2"), lat=node.lat if node else reconcile._float(record.get("lat")), lon=node.lon if node else reconcile._float(record.get("lon")), ids={"model": node.code} if node else {}, status="open", history=[{"at": now, "change": "opened" if node is None else "registered", "detail": f"{'New from' if node is None else 'Matched by'} {source.filename}", "by": by}])
        session.add(row)
    ids = dict(row.ids or {})
    if record.get("code"):
        ids[label] = str(record["code"])
    for key, value in (record.get("external_ids") or {}).items():
        ids[key] = str(value)
    history = list(row.history or [])
    if record.get("name") and reconcile.clean(record["name"]) != reconcile.clean(row.name):
        history.append({"at": now, "change": "renamed" if authoritative else "also_known_as", "detail": f"{row.name!r} is {record['name']!r} in {source.filename}", "by": by})
        if authoritative:
            row.name = str(record["name"])
    if record.get("type") and row.type and str(record["type"]).lower() != row.type.lower():
        history.append({"at": now, "change": "recategorised" if authoritative else "type_differs", "detail": f"{row.type} vs {record['type']} in {source.filename}", "by": by})
    status = str(record.get("operating_status") or "").lower()
    if status in ("closed", "non_operational") and row.status == "open":
        history.append({"at": now, "change": "closed", "detail": f"Reported {status} in {source.filename}", "by": by})
        if authoritative:
            row.status = "closed"
    row.ids = ids
    row.history = history
    if row.lat is None and record.get("lat") not in (None, ""):
        row.lat, row.lon = reconcile._float(record.get("lat")), reconcile._float(record.get("lon"))
    session.flush()
    return row


def decide_match(session: Session, run: OnboardingRun, match: FacilityMatch, *, choice: str, by: str, comment: str = "") -> FacilityMatch:
    """FR10: the arbiter's decision becomes the source. ``choice`` is a candidate code or
    ``__new__``."""
    _open(run)
    source = session.get(SourceFile, match.source_file_id)
    pack = run_pack(session, run)
    if choice == "__new__":
        canonical = str(match.source_record.get("code") or "").strip() or _slug(match.source_name)
        if session.scalar(select(Crosswalk).where(Crosswalk.country_id == run.country_id, Crosswalk.canonical_code == canonical)):
            canonical = f"{canonical}-{match.source_file_id}"
        match.decision, match.canonical_code = "new", canonical
    else:
        if choice not in {c["code"] for c in match.candidates}:
            raise OnboardingError("That candidate is not one of the options for this facility.")
        match.decision, match.canonical_code = "accepted", choice
    match.decided_by, match.decided_at, match.reason = by, datetime.now(timezone.utc), (comment or f"Decided by {by}: {match.reason}")
    _accept_into_crosswalk(session, run, match, match.source_record, source, by=by)
    raw = {"fields": {k: v for k, v in match.source_record.items() if k != "external_ids"}, "locations": {}, "aux": {}}
    for key, value in (match.source_record.get("external_ids") or {}).items():
        raw["fields"][key] = value
    common = dict(source_file_id=source.id if source else None, system=(source.source_system if source else "file"), vintage="")
    _stage_node_record(session, run, source, match.canonical_code, match.source_record, raw, pack, common, {}, {}, False)
    for item in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.kind == "match", ReviewItem.subject.like(f"match:{match.id} %"))):
        item.decision, item.choice, item.decided_by, item.decided_at, item.comment = "chosen", choice, by, datetime.now(timezone.utc), comment
    session.flush()
    _conflict_items(session, run, "Nodes")
    return match


# --- conflicts ------------------------------------------------------------------------------


def _conflict_items(session: Session, run: OnboardingRun, domain: str) -> None:
    """FR15: one review item per conflicted field, with both values as options."""
    existing = {i.subject for i in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.kind == "conflict", ReviewItem.decision == "pending"))}
    for record in session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id, StagedRecord.domain == domain, StagedRecord.status == "conflict")):
        for name, item in (record.fields or {}).items():
            if not item.get("alternatives"):
                continue
            subject = f"{domain}:{record.key}/{name}"
            if subject in existing:
                continue
            options = [{"index": 0, "label": f"{item.get('value')} ({_source_label(session, item)})"}] + [{"index": i + 1, "label": f"{a.get('value')} ({_source_label(session, a)})"} for i, a in enumerate(item["alternatives"])]
            session.add(ReviewItem(run_id=run.id, kind="conflict", subject=subject, title=f"{record.label or record.key}: sources disagree on {name}", detail="Two sources give different values. Choose which the model should use; the other stays on the record as rejected.", confidence="low", impact=_impact_of(record, name), payload={"record_id": record.id, "field": name, "file_subjects": [f"file:{item.get('source_file_id')}"] + [f"file:{a.get('source_file_id')}" for a in item["alternatives"]]}, options=options))
    session.flush()


def _source_label(session: Session, item: dict) -> str:
    source = session.get(SourceFile, item.get("source_file_id")) if item.get("source_file_id") else None
    return f"{source.filename}, {item.get('location') or 'row'}" if source else item.get("system") or "source"


def _impact_of(record: StagedRecord, name: str) -> float:
    try:
        if record.domain == "Demand":
            return abs(float(value_of(record, "quantity") or 0))
        if record.domain == "Nodes":
            return float(value_of(record, "catchment_population") or 0) or 1.0
        return abs(float(value_of(record, name) or 0)) or 1.0
    except (TypeError, ValueError):
        return 1.0


# --- rules and estimates -------------------------------------------------------------------


def build_context(session: Session, run: OnboardingRun) -> rules.Context:
    pack = run_pack(session, run)
    country = session.get(Country, run.country_id)
    records = list(session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id)))
    products = {p.sku: p for p in session.scalars(select(Product).where(Product.country_id == country.id))}
    volume = {sku: float(p.volume_per_unit_cm3 or 0) for sku, p in products.items()}
    for r in records:
        if r.domain == "Products" and value_of(r, "volume_per_unit_cm3") is not None:
            volume[r.key] = float(value_of(r, "volume_per_unit_cm3"))
    nodes = {n.code: n for n in session.scalars(select(Node).where(Node.country_id == country.id))}
    population = {code: float(n.catchment_population or 0) for code, n in nodes.items()}
    storage = {code: float((n.capacity or {}).get("dry_m3") or 0) for code, n in nodes.items()}
    for r in records:
        if r.domain == "Nodes":
            if value_of(r, "catchment_population") is not None:
                population[r.key] = float(value_of(r, "catchment_population"))
            if value_of(r, "dry_m3") is not None:
                storage[r.key] = float(value_of(r, "dry_m3"))
    cover = float(((country.config or {}).get("estimators") or {}).get("cover_days") or 30.0)
    return rules.Context(pack=pack, product_volume_cm3=volume, population=population, storage_m3=storage, previous=_previous_values(session, run), cover_days=cover)


def _previous_values(session: Session, run: OnboardingRun) -> Dict[tuple, object]:
    """The approved dataset as the model holds it now, keyed like staging."""
    country_id = run.country_id
    out: Dict[tuple, object] = {}
    nodes = {n.id: n for n in session.scalars(select(Node).where(Node.country_id == country_id))}
    for n in nodes.values():
        out[("Nodes", n.code, "catchment_population")] = n.catchment_population
        out[("Nodes", n.code, "dry_m3")] = (n.capacity or {}).get("dry_m3")
        out[("Nodes", n.code, "lat")] = n.lat
        out[("Nodes", n.code, "lon")] = n.lon
        out[("Nodes", n.code, "name")] = n.name
    products = {p.id: p for p in session.scalars(select(Product).where(Product.country_id == country_id))}
    for p in products.values():
        out[("Products", p.sku, "volume_per_unit_cm3")] = p.volume_per_unit_cm3
        out[("Products", p.sku, "unit_cost")] = p.unit_cost
    for d in session.scalars(select(Demand).where(Demand.country_id == country_id)):
        node, product = nodes.get(d.node_id), products.get(d.product_id)
        if node and product and d.provenance_class != "illustrative":
            out[("Demand", f"{node.code}|{product.sku}|{d.period}", "quantity")] = d.quantity
    return out


def run_rules(session: Session, run: OnboardingRun) -> dict:
    """FR16-FR21: check everything staged, write the flags on the records and one review
    item per warning or error."""
    _open(run)
    context = build_context(session, run)
    records = list(session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id)))
    _expected_demand(session, run, records, context)
    _expected_storage(session, run, records)
    records = list(session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id)))
    issues = rules.run_checks(records, context)
    by_key: Dict[tuple, list] = {}
    for issue in issues:
        by_key.setdefault((issue.domain, issue.key), []).append(issue.as_dict())
    for old in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.kind == "anomaly", ReviewItem.decision == "pending")):
        session.delete(old)
    for record in records:
        kept = [i for i in (record.issues or []) if i.get("code") in ("unresolved_reference",)]
        record.issues = kept + by_key.get((record.domain, record.key), [])
    for issue in issues:
        if issue.severity == rules.INFO:
            continue
        session.add(ReviewItem(run_id=run.id, kind="anomaly", subject=f"{issue.domain}:{issue.key}/{issue.field}", title=issue.message[:255], detail=issue.suggestion, confidence="low" if issue.severity == rules.ERROR else "medium", impact=issue.impact, payload=issue.as_dict(), options=[{"code": "keep", "label": "The value is right; keep it"}, {"code": "drop", "label": "The value is wrong; mark it missing"}, {"code": "correct", "label": "Type the right value"}]))
    baseline = rules.baseline_total_check(records, context)
    run.summary = {**(run.summary or {}), "checks": {"issues": len(issues), "errors": sum(1 for i in issues if i.severity == rules.ERROR), "warnings": sum(1 for i in issues if i.severity == rules.WARNING), "baseline_total": baseline, "at": datetime.now(timezone.utc).isoformat()}}
    session.flush()
    return run.summary["checks"]


def _expected_demand(session: Session, run: OnboardingRun, records: List[StagedRecord], context: rules.Context) -> None:
    """FR24: a facility with no figure for a product other facilities report is a gap to
    fill or flag, not a row to leave out. Only once a demand file has been staged."""
    demand = [r for r in records if r.domain == "Demand" and not r.key.startswith("?")]
    if not demand:
        return
    skus = {rules._split_key(r.key)[1] for r in demand}
    periods = {rules._split_key(r.key)[2] for r in demand}
    period = 0 if 0 in periods else None
    if period is None:
        return
    have = {r.key for r in demand}
    facilities = [r for r in records if r.domain == "Nodes" and str(value_of(r, "hub_capable") or "").lower() not in ("true", "1", "yes")]
    for facility in facilities:
        for sku in skus:
            key = f"{facility.key}|{sku}|0"
            if key in have:
                continue
            session.add(StagedRecord(run_id=run.id, domain="Demand", key=key, label=f"{facility.label or facility.key} / {sku}", fields={"quantity": missing(f"no figure for {sku} in any source")}, aux={}, issues=[], status="blocked"))
    session.flush()


def _expected_storage(session: Session, run: OnboardingRun, records: List[StagedRecord]) -> None:
    """Once any source has said how much a facility can store, a facility it says nothing
    about is a gap to fill by cover days or to flag, not a silence to keep."""
    nodes = [r for r in records if r.domain == "Nodes"]
    if not any("dry_m3" in (r.fields or {}) for r in nodes):
        return
    for record in nodes:
        fields = copy.deepcopy(record.fields or {})
        if "dry_m3" in fields or str(value_of(record, "hub_capable") or "").lower() in ("true", "1", "yes"):
            continue
        fields["dry_m3"] = missing("no storage figure in any source")
        record.fields = fields
        record.status = "blocked" if record.status != "conflict" else record.status
    session.flush()


def run_estimates(session: Session, run: OnboardingRun) -> dict:
    """FR22-FR23: propose a named method for every gap; each proposal is its own review item."""
    _open(run)
    context = build_context(session, run)
    records = list(session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id)))
    node_types = {r.key: str(value_of(r, "type") or "") for r in records if r.domain == "Nodes"}
    for node in session.scalars(select(Node).where(Node.country_id == run.country_id)):
        node_types.setdefault(node.code, node.type or "")
    proposals = estimating.propose(records, context, node_types=node_types)
    for old in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.kind == "estimate", ReviewItem.decision == "pending")):
        session.delete(old)
    by_key = {(r.domain, r.key): r for r in records}
    for proposal in proposals:
        record = by_key[(proposal.domain, proposal.key)]
        session.add(ReviewItem(run_id=run.id, kind="estimate", subject=f"{proposal.domain}:{proposal.key}/{proposal.field}", title=f"{record.label or record.key}: estimate {proposal.field} by {estimating.METHODS[proposal.method]['label'].lower()}", detail=f"{proposal.formula}. Directional, not for budgeting.", confidence=proposal.confidence, impact=abs(float(proposal.value or 0)), payload={"record_id": record.id, "field": proposal.field, "method": proposal.method, "value": proposal.value, "inputs": proposal.inputs, "formula": proposal.formula}, options=[{"code": "accept", "label": "Use this estimate"}, {"code": "reject", "label": "Leave it missing"}]))
    unfillable = [r for r in records if r.domain == "Demand" and (r.fields.get("quantity") or {}).get("class") == "missing" and (r.domain, r.key) not in {(p.domain, p.key) for p in proposals}]
    existing = {i.subject for i in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.kind == "missing", ReviewItem.decision == "pending"))}
    for record in unfillable:
        subject = f"Demand:{record.key}/quantity"
        if subject not in existing:
            session.add(ReviewItem(run_id=run.id, kind="missing", subject=subject, title=f"{record.label or record.key}: no figure and no defensible method", detail="No source has a value and no estimation method has the inputs it needs. Type a confirmed figure, or the gap blocks sign-off.", confidence="low", impact=1.0, payload={"record_id": record.id, "field": "quantity"}, options=[{"code": "correct", "label": "Type a confirmed figure"}]))
    run.summary = {**(run.summary or {}), "estimates": {"proposed": len(proposals), "unfillable": len(unfillable), "by_method": _count(p.method for p in proposals)}}
    session.flush()
    return run.summary["estimates"]


def _count(values: Iterable[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out
