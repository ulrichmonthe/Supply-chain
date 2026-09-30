"""The evaluation set: the PNG reference data, degraded on purpose, with the answers kept.

Columns renamed and reordered, a title row above the header, units mixed, facility
names and codes altered the way real registers alter them, a share of codes removed
altogether, anomalies and conflicts planted. The pipeline runs over it and is scored on
what the PRD asks for: facility match precision and recall, mapping precision, planted
anomalies caught, conflicts surfaced rather than resolved, and values that would leave
with the wrong provenance class -- which must be zero.

Seeded, so the same degradation and the same scores come out every time.
"""

from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..io import mapper
from ..models import Country, Demand, FacilityMatch, Node, Product, ReviewItem, StagedRecord
from . import agent as agents, pack as packs, service, staging
from .review import decide, queue as review_queue

#: How a ministry register writes the words the model uses.
RENAMES = [
    ("Health Centre", "H/C"), ("Health Centre", "HC"), ("Health Centre", "Health Center"), ("Hospital", "Hosp."),
    ("General Hospital", "Gen. Hospital"), ("District Hospital", "Dist. Hospital"), ("Sub Health Centre", "Sub-Health Centre"),
    ("Aid Post", "A/P"), ("Mount ", "Mt "), ("Saint ", "St "),
]
FOREIGN_HEADERS = {
    "code": "HF Code", "name": "Health Facility Name", "admin1": "Province", "admin2": "District", "lat": "GPS Lat", "lon": "GPS Long",
    "catchment_population": "Pop. Served", "operating_status": "Functional Status", "type": "Facility Type",
}


@dataclass
class Truth:
    """What the degraded rows really are."""

    code_by_row_code: Dict[str, str] = field(default_factory=dict)  # degraded code -> true code
    genuinely_new: set = field(default_factory=set)
    anomalies: Dict[str, str] = field(default_factory=dict)  # demand key -> what was planted
    conflicts: set = field(default_factory=set)  # node codes with a planted disagreement
    per_1000: Dict[str, float] = field(default_factory=dict)


def degrade(session: Session, country: Country, *, seed: int = 7, facility_share: float = 1.0) -> Tuple[Dict[str, bytes], Truth]:
    """Files as a ministry would hand them over, and the truth to score against."""
    rng = random.Random(seed)
    truth = Truth()
    nodes = [n for n in session.scalars(select(Node).where(Node.country_id == country.id)) if n.level == 3]
    products = {p.id: p for p in session.scalars(select(Product).where(Product.country_id == country.id))}
    demand = list(session.scalars(select(Demand).where(Demand.country_id == country.id, Demand.period == 0)))
    rng.shuffle(nodes)
    nodes = nodes[: max(10, int(len(nodes) * facility_share))]

    # --- the facility register: renamed, some codes missing, some retyped, a title row -------
    rows = []
    for node in nodes:
        name = node.name
        for old, new in RENAMES:
            if old in name and rng.random() < 0.6:
                name = name.replace(old, new)
                break
        roll = rng.random()
        if roll < 0.15:
            code = ""  # no code at all: name, province and coordinates must carry it
        elif roll < 0.30:
            code = "HMIS-" + node.code.split("-", 1)[1]  # the same code in another scheme
        else:
            code = node.code
        if code:
            truth.code_by_row_code[code] = node.code
        else:
            truth.code_by_row_code[name] = node.code
        lat, lon = node.lat, node.lon
        if rng.random() < 0.2:
            lat, lon = lat + rng.uniform(-0.01, 0.01), lon + rng.uniform(-0.01, 0.01)  # a better or worse GPS fix
        population = node.catchment_population
        rows.append({"HF Code": code, "Health Facility Name": name, "Province": node.admin1 or "", "District": node.admin2 or "", "GPS Lat": lat, "GPS Long": lon, "Pop. Served": int(population), "Functional Status": node.operating_status, "Facility Type": node.type})
    # Genuinely new facilities, absent from the model.
    for i in range(3):
        base = rng.choice(nodes)
        code = f"NEW-{i + 1:03d}"
        truth.genuinely_new.add(code)
        rows.append({"HF Code": code, "Health Facility Name": f"{base.admin2 or base.admin1} Outreach Post {i + 1}", "Province": base.admin1 or "", "District": base.admin2 or "", "GPS Lat": base.lat + 0.3, "GPS Long": base.lon + 0.3, "Pop. Served": 1500 + i * 400, "Functional Status": "operational", "Facility Type": "aid_post"})
    header = list(FOREIGN_HEADERS.values())
    rng.shuffle(header)
    register = io.StringIO()
    register.write("National Health Facility Register - export 2025-06;;;;;;;;\n")  # a title row above the header
    writer = csv.DictWriter(register, fieldnames=header, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    # --- a second list that disagrees on a few populations ------------------------------
    second_rows = []
    for row in rows[: len(rows) // 2]:
        copy = dict(row)
        if rng.random() < 0.2 and copy["HF Code"]:
            copy["Pop. Served"] = int(copy["Pop. Served"] * rng.choice([1.5, 0.6, 2.0]))
            truth.conflicts.add(truth.code_by_row_code.get(copy["HF Code"] or copy["Health Facility Name"], ""))
        second_rows.append({"facility_code": copy["HF Code"], "facility_name": copy["Health Facility Name"], "province": copy["Province"], "latitude": copy["GPS Lat"], "longitude": copy["GPS Long"], "population": copy["Pop. Served"]})
    second = io.StringIO()
    writer = csv.DictWriter(second, fieldnames=["facility_code", "facility_name", "province", "latitude", "longitude", "population"])
    writer.writeheader()
    for row in second_rows:
        writer.writerow(row)

    # --- consumption in mSupply's shape, with anomalies and a stock-out planted -------------
    by_node_code = {n.code: n for n in nodes}
    degraded_code = {v: k for k, v in truth.code_by_row_code.items()}
    consumption = io.StringIO()
    writer = csv.writer(consumption)
    writer.writerow(["store_code", "item_code", "month", "adjusted_monthly_consumption", "days_out_of_stock"])
    planted = 0
    for row in demand:
        node = by_node_code.get(next((n.code for n in nodes if n.id == row.node_id), None))
        product = products.get(row.product_id)
        if not node or not product:
            continue
        if rng.random() < 0.15:
            continue  # a facility that did not report this product: a gap to fill
        quantity = row.quantity
        stockout = 0
        key = f"{node.code}|{product.sku}|0"
        roll = rng.random()
        if roll < 0.04 and planted < 12:
            quantity = quantity * 40  # typed in units instead of packs
            truth.anomalies[key] = "x40"
            planted += 1
        elif roll < 0.10:
            stockout = rng.choice([45, 90, 120])
            quantity = quantity * (1 - stockout / 365)
        store = degraded_code.get(node.code, node.code) or node.code
        writer.writerow([store, product.sku, "2025", round(quantity, 1), stockout])
        truth.per_1000[product.sku] = 0.0
    return {"facility_register.csv": register.getvalue().encode("utf-8"), "epi_list.csv": second.getvalue().encode("utf-8"), "msupply_2025.csv": consumption.getvalue().encode("utf-8")}, truth


def run(session: Session, country: Country, *, seed: int = 7, facility_share: float = 1.0, use_agent: bool = False) -> dict:
    """Degrade, onboard with the rules only (or the agent, when allowed), and score."""
    files, truth = degrade(session, country, seed=seed, facility_share=facility_share)
    pack = packs.ensure(session, country)
    run_ = service.create_run(session, country, name=f"evaluation seed {seed}", preparer="evaluation harness", kind="initial")
    proposer = agents.proposer(packs.validate(pack.pack)) if use_agent else agents.RulesProposer()

    scores: Dict[str, object] = {"seed": seed, "proposer": proposer.name}

    # 1. the register: mapping precision, then reconciliation.
    register = service.add_file(session, run_, filename="facility_register.csv", data=files["facility_register.csv"], uploader="evaluation harness")
    sheet = register.profile["sheets"][0]
    proposals = proposer.propose_mappings("Nodes", sheet["columns"], sheet["sample"])
    expected = {FOREIGN_HEADERS[k]: k for k in FOREIGN_HEADERS}
    right = sum(1 for p in proposals if expected.get(p["column"]) == p["field"])
    scores["mapping"] = {"proposed": len(proposals), "right": right, "precision": round(right / len(proposals), 3) if proposals else 0.0, "recall": round(right / len(expected), 3), "wrong": [{"column": p["column"], "proposed": p["field"], "truth": expected.get(p["column"])} for p in proposals if expected.get(p["column"]) != p["field"]]}
    mapping = {p["field"]: p["column"] for p in proposals}
    service.set_mapping(session, run_, register, mapping=mapping, by="evaluation harness")
    counts = service.stage_file(session, run_, register, by="evaluation harness")

    matches = list(session.scalars(select(FacilityMatch).where(FacilityMatch.run_id == run_.id, FacilityMatch.source_file_id == register.id)))
    auto = [m for m in matches if m.decision == "auto_accepted"]
    auto_right = sum(1 for m in auto if truth.code_by_row_code.get(m.source_key or m.source_name) == m.canonical_code)
    new = [m for m in matches if m.decision == "new"]
    new_right = sum(1 for m in new if m.source_key in truth.genuinely_new)
    pending = [m for m in matches if m.decision == "pending"]
    should_match = [m for m in matches if (m.source_key or m.source_name) in truth.code_by_row_code]
    pending_resolvable = sum(1 for m in pending if any(c["code"] == truth.code_by_row_code.get(m.source_key or m.source_name) for c in m.candidates))
    scores["facilities"] = {
        "rows": len(matches), "auto_accepted": len(auto), "auto_precision": round(auto_right / len(auto), 4) if auto else None,
        "auto_recall": round(auto_right / len(should_match), 4) if should_match else None,
        "new": len(new), "new_right": new_right, "pending": len(pending), "pending_with_right_candidate": pending_resolvable,
        "wrong_auto_accepts": [{"source": m.source_key or m.source_name, "chose": m.canonical_code, "truth": truth.code_by_row_code.get(m.source_key or m.source_name)} for m in auto if truth.code_by_row_code.get(m.source_key or m.source_name) != m.canonical_code],
        "share_without_review": round(len(auto) / len(matches), 3) if matches else 0.0,
    }
    # The arbiter's work, simulated with the truth so the rest of the pipeline can run.
    for m in pending:
        right_code = truth.code_by_row_code.get(m.source_key or m.source_name)
        choice = right_code if right_code and any(c["code"] == right_code for c in m.candidates) else "__new__"
        service.decide_match(session, run_, m, choice=choice, by="evaluation arbiter")

    # 2. the second list: conflicts must surface, never be averaged.
    second = service.add_file(session, run_, filename="epi_list.csv", data=files["epi_list.csv"], uploader="evaluation harness")
    guess = mapper.guess_mapping(second.profile["sheets"][0]["columns"], "Nodes")
    service.set_mapping(session, run_, second, mapping=guess, by="evaluation harness")
    service.stage_file(session, run_, second, by="evaluation harness")
    for m in session.scalars(select(FacilityMatch).where(FacilityMatch.run_id == run_.id, FacilityMatch.source_file_id == second.id, FacilityMatch.decision == "pending")):
        right_code = truth.code_by_row_code.get(m.source_key or m.source_name)
        service.decide_match(session, run_, m, choice=right_code if right_code and any(c["code"] == right_code for c in m.candidates) else "__new__", by="evaluation arbiter")
    conflict_items = list(session.scalars(select(ReviewItem).where(ReviewItem.run_id == run_.id, ReviewItem.kind == "conflict")))
    surfaced = {i.subject.split(":")[1].split("/")[0] for i in conflict_items}
    planted_conflicts = {c for c in truth.conflicts if c}
    scores["conflicts"] = {"planted": len(planted_conflicts), "surfaced": len(planted_conflicts & surfaced), "silently_resolved": len(planted_conflicts - surfaced), "extra": len(surfaced - planted_conflicts)}

    # 3. consumption: anomalies caught, estimates proposed.
    consumption = service.add_file(session, run_, filename="msupply_2025.csv", data=files["msupply_2025.csv"], uploader="evaluation harness")
    service.stage_file(session, run_, consumption, by="evaluation harness")
    checks = service.run_rules(session, run_)
    anomaly_items = list(session.scalars(select(ReviewItem).where(ReviewItem.run_id == run_.id, ReviewItem.kind == "anomaly")))
    flagged_keys = {i.subject.split(":", 1)[1].rsplit("/", 1)[0] for i in anomaly_items}
    caught = sum(1 for key in truth.anomalies if key in flagged_keys)
    scores["anomalies"] = {"planted": len(truth.anomalies), "caught": caught, "recall": round(caught / len(truth.anomalies), 3) if truth.anomalies else None, "flags_total": len(anomaly_items), "checks": checks}
    estimates = service.run_estimates(session, run_)
    scores["estimates"] = estimates

    # 4. provenance: nothing leaves with the wrong class.
    records = list(session.scalars(select(StagedRecord).where(StagedRecord.run_id == run_.id)))
    wrong_class = 0
    for record in records:
        for name, item in (record.fields or {}).items():
            cls = item.get("class")
            if cls == "estimated" and not item.get("method"):
                wrong_class += 1
            if cls == "converted" and not item.get("transformation"):
                wrong_class += 1
            if cls == "observed" and not item.get("source_file_id"):
                wrong_class += 1
            if cls == "confirmed" and not item.get("by"):
                wrong_class += 1
    scores["provenance"] = {**staging.coverage(records), "wrong_class": wrong_class}
    scores["queue"] = {k: v for k, v in review_queue(session, run_, limit=0).items() if k != "items"}
    scores["targets"] = {
        "auto_precision_at_least_0.98": (scores["facilities"]["auto_precision"] or 0) >= 0.98,
        "anomalies_caught_at_least_0.90": (scores["anomalies"]["recall"] or 0) >= 0.90,
        "no_conflict_silently_resolved": scores["conflicts"]["silently_resolved"] == 0,
        "no_value_with_wrong_class": wrong_class == 0,
    }
    scores["passes"] = all(scores["targets"].values())
    session.flush()
    return scores
