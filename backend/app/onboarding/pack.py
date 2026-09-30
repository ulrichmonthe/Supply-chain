"""The country pack: everything that differs between countries and cannot live in code.

Completed during the data landscape sprint, reviewed by the ministry, versioned, and
changed only with approval. Ingestion refuses to start until the pack the run uses is
approved and every section a pipeline step depends on is filled in. The Papua New
Guinea reference workspace ships as the first worked example, marked as such.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Country, CountryPack


class SourceSystem(BaseModel):
    name: str
    system: str = "excel"  # dhis2 | msupply | openlmis | excel | csv | other
    level: str = ""  # central | provincial | facility
    version: str = ""
    cadence: str = ""  # monthly | quarterly | on request
    export_format: str = ""
    owner: str = ""
    domains: List[str] = Field(default_factory=list)  # Nodes | Demand | ...


class FacilityAuthority(BaseModel):
    authoritative_list: str = ""  # name of the source that wins, or "" for none
    arbiter: str = ""  # the named person who decides unresolved matches
    id_schemes: List[str] = Field(default_factory=list)
    geolocation_allowed: bool = True


class AdminStructure(BaseModel):
    levels: List[str] = Field(default_factory=lambda: ["province", "district"])


class NetworkStructure(BaseModel):
    tiers: List[str] = Field(default_factory=list)
    hubs: List[str] = Field(default_factory=list)
    direct_delivery_rules: str = ""


class ModeRange(BaseModel):
    speed_kmh: List[float] = Field(default_factory=lambda: [5.0, 120.0])
    cost_per_m3: List[float] = Field(default_factory=lambda: [0.0, 1e7])
    seasonal: bool = False


class Transport(BaseModel):
    modes: dict = Field(default_factory=dict)  # mode -> ModeRange
    seasonal_pattern: str = ""
    hidden_costs: List[str] = Field(default_factory=list)


class Conversion(BaseModel):
    """A unit the sources use and how to reach the model's unit."""

    from_unit: str
    to_unit: str
    factor: float
    note: str = ""


class ProductsUnits(BaseModel):
    product_aliases: dict = Field(default_factory=dict)  # their code -> our sku
    conversions: List[Conversion] = Field(default_factory=list)
    per_1000_rates: dict = Field(default_factory=dict)  # sku -> annual units per 1,000 people
    per_consultation: dict = Field(default_factory=dict)  # sku -> units per consultation


class Currency(BaseModel):
    code: str = "USD"
    base_year: int = 2026
    fx_source: str = ""
    fx_to_model: dict = Field(default_factory=dict)  # "USD" -> factor into model currency


class LegalProfile(BaseModel):
    data_residency: str = ""  # in-country | any | unknown
    external_ai_allowed: bool = False
    data_sharing_agreement: str = ""  # signed | draft | none
    signatory: str = ""


class Roles(BaseModel):
    data_officers: List[str] = Field(default_factory=list)
    approvers: List[str] = Field(default_factory=list)
    local_contacts: dict = Field(default_factory=dict)  # admin1 -> contact


class Thresholds(BaseModel):
    auto_accept_match: float = 0.92
    review_match_floor: float = 0.45
    change_sensitivity: float = 0.5  # relative change vs the approved dataset that flags
    ranges: dict = Field(default_factory=dict)  # field -> [min, max]
    known_aggregates: dict = Field(default_factory=dict)  # "annual_demand_m3" -> figure


class Pack(BaseModel):
    source_systems: List[SourceSystem] = Field(default_factory=list)
    facility_authority: FacilityAuthority = Field(default_factory=FacilityAuthority)
    admin_structure: AdminStructure = Field(default_factory=AdminStructure)
    network_structure: NetworkStructure = Field(default_factory=NetworkStructure)
    transport: Transport = Field(default_factory=Transport)
    products_units: ProductsUnits = Field(default_factory=ProductsUnits)
    currency: Currency = Field(default_factory=Currency)
    legal_profile: LegalProfile = Field(default_factory=LegalProfile)
    roles: Roles = Field(default_factory=Roles)
    languages: List[str] = Field(default_factory=lambda: ["en"])
    thresholds: Thresholds = Field(default_factory=Thresholds)
    reference_example: bool = False


#: What each pipeline step needs before it may run. The gate is the whole list.
REQUIRED = [
    ("source_systems", "at least one source system named", lambda p: bool(p.source_systems)),
    ("facility_authority.arbiter", "a named facility arbiter", lambda p: bool(p.facility_authority.arbiter.strip())),
    ("admin_structure.levels", "the admin levels", lambda p: bool(p.admin_structure.levels)),
    ("transport.modes", "cost and speed ranges per mode", lambda p: bool(p.transport.modes)),
    ("currency.code", "the model currency", lambda p: bool(p.currency.code)),
    ("legal_profile.data_residency", "where the data may live", lambda p: bool(p.legal_profile.data_residency)),
    ("roles.data_officers", "a named data officer", lambda p: bool(p.roles.data_officers)),
    ("roles.approvers", "a named approver", lambda p: bool(p.roles.approvers)),
    ("thresholds.auto_accept_match", "an auto-accept threshold between 0.8 and 1", lambda p: 0.8 <= p.thresholds.auto_accept_match <= 1.0),
]


def validate(raw: dict) -> Pack:
    return Pack.model_validate(raw or {})


def completeness(pack: Pack) -> dict:
    missing = [{"path": path, "needs": needs} for path, needs, ok in REQUIRED if not ok(pack)]
    return {"complete": not missing, "missing": missing, "checked": len(REQUIRED)}


def png_example(country: Country) -> dict:
    """The PNG reference pack, built from the seeded country config. Marked as an example:
    every name in it is a placeholder until the ministry replaces it."""
    config = country.config or {}
    rates = (config.get("estimators") or {}).get("per_1000") or {}
    return Pack(
        source_systems=[
            SourceSystem(name="NDoH master facility list (Excel)", system="excel", level="central", cadence="on request", export_format="xlsx", owner="NDoH Health Information", domains=["Nodes"]),
            SourceSystem(name="mSupply, Area Medical Stores", system="msupply", level="central", cadence="monthly", export_format="csv", owner="Medical Supplies Branch", domains=["Demand", "Products"]),
            SourceSystem(name="eNHIS (DHIS2)", system="dhis2", level="provincial", cadence="monthly", export_format="csv", owner="NDoH Health Information", domains=["Nodes", "Demand"]),
            SourceSystem(name="Cold chain equipment inventory", system="excel", level="central", cadence="annual", export_format="xlsx", owner="EPI programme", domains=["Nodes"]),
        ],
        facility_authority=FacilityAuthority(authoritative_list="", arbiter="Facility arbiter (to be named by NDoH)", id_schemes=["NDoH facility code", "eNHIS org unit UID", "mSupply store code"], geolocation_allowed=True),
        admin_structure=AdminStructure(levels=["province", "district"]),
        network_structure=NetworkStructure(tiers=[config.get("level_labels", {}).get(str(i)) or config.get("level_labels", {}).get(i) or f"level {i}" for i in range(4)], hubs=["AMS-POM", "AMS-LAE", "AMS-MAD", "AMS-HGN", "AMS-KOK"], direct_delivery_rules="Hospitals are supplied from the Area Medical Store of their region; islands by scheduled sea or air service."),
        transport=Transport(
            modes={
                "road": ModeRange(speed_kmh=[8, 90], cost_per_m3=[20, 2000]).model_dump(),
                "sea": ModeRange(speed_kmh=[6, 30], cost_per_m3=[50, 3000], seasonal=True).model_dump(),
                "air": ModeRange(speed_kmh=[120, 450], cost_per_m3=[800, 12000], seasonal=True).model_dump(),
                "river": ModeRange(speed_kmh=[5, 25], cost_per_m3=[40, 2500], seasonal=True).model_dump(),
            },
            seasonal_pattern="North-west monsoon December to March closes highland roads and exposes sea lanes.",
            hidden_costs=["Facility staff paying for PMV transport to collect stock", "Charter flights booked outside the contract"],
        ),
        products_units=ProductsUnits(
            product_aliases={},
            conversions=[
                Conversion(from_unit="litres", to_unit="m3", factor=0.001, note="storage volume"),
                Conversion(from_unit="l", to_unit="m3", factor=0.001, note="storage volume"),
                Conversion(from_unit="cm3", to_unit="m3", factor=1e-6),
                Conversion(from_unit="km", to_unit="km", factor=1.0),
                Conversion(from_unit="miles", to_unit="km", factor=1.609344),
                Conversion(from_unit="minutes", to_unit="hours", factor=1 / 60),
                Conversion(from_unit="days", to_unit="hours", factor=24.0),
            ],
            per_1000_rates={sku: float(rate) for sku, rate in rates.items()},
            per_consultation={},
        ),
        currency=Currency(code=country.currency or "PGK", base_year=2026, fx_source="Bank of PNG monthly average", fx_to_model={"USD": 3.9, "AUD": 2.6}),
        legal_profile=LegalProfile(data_residency="in-country", external_ai_allowed=False, data_sharing_agreement="none", signatory=""),
        roles=Roles(data_officers=["Data officer (to be named)"], approvers=["Approver (to be named)"], local_contacts={}),
        languages=["en", "tpi"],
        thresholds=Thresholds(
            auto_accept_match=0.92,
            review_match_floor=0.45,
            change_sensitivity=0.5,
            ranges={
                "catchment_population": [0, 1_500_000],
                "dry_m3": [0, 15_000],
                "cold_2_8_m3": [0, 500],
                "distance_km": [0, 2_500],
                "base_travel_time_hr": [0, 240],
                "demand_per_1000": [0, 1_000],
            },
            known_aggregates={},
        ),
        reference_example=True,
    ).model_dump()


def current(session: Session, country: Country) -> Optional[CountryPack]:
    """The approved pack in force, or the latest draft if none is approved."""
    packs = list(session.scalars(select(CountryPack).where(CountryPack.country_id == country.id).order_by(CountryPack.version.desc())))
    for pack in packs:
        if pack.status == "approved":
            return pack
    return packs[0] if packs else None


def ensure(session: Session, country: Country) -> CountryPack:
    """A pack exists for every country: the PNG example for PNG, an empty draft otherwise."""
    pack = current(session, country)
    if pack:
        return pack
    if country.code == "PNG":
        pack = CountryPack(country_id=country.id, version=1, status="approved", pack=png_example(country), note="Reference example shipped with the tool. Every name and threshold is a placeholder until the ministry replaces it.", author_claim="reference example", approved_by="reference example (illustrative)", approved_at=datetime.now(timezone.utc))
    else:
        pack = CountryPack(country_id=country.id, version=1, status="draft", pack=Pack().model_dump(), author_claim="system")
    session.add(pack)
    session.flush()
    return pack


def new_version(session: Session, country: Country, raw: dict, *, author: str, note: str = "") -> CountryPack:
    validated = validate(raw)
    latest = session.scalar(select(CountryPack).where(CountryPack.country_id == country.id).order_by(CountryPack.version.desc()))
    pack = CountryPack(country_id=country.id, version=(latest.version + 1) if latest else 1, status="draft", pack=validated.model_dump(), note=note, author_claim=author)
    session.add(pack)
    session.flush()
    return pack


def approve(session: Session, pack: CountryPack, *, approver: str) -> CountryPack:
    """Approval needs a complete pack and a different person from its author."""
    check = completeness(validate(pack.pack))
    if not check["complete"]:
        needs = ", ".join(m["needs"] for m in check["missing"])
        raise ValueError(f"The pack is not complete: it still needs {needs}.")
    if approver.strip().lower() == (pack.author_claim or "").strip().lower():
        raise ValueError("The person who wrote the pack cannot approve it. Ask a reviewer to sign.")
    for other in session.scalars(select(CountryPack).where(CountryPack.country_id == pack.country_id, CountryPack.status == "approved")):
        other.status = "superseded"
    pack.status = "approved"
    pack.approved_by = approver
    pack.approved_at = datetime.now(timezone.utc)
    session.flush()
    return pack


def as_dict(pack: CountryPack) -> dict:
    validated = validate(pack.pack)
    return {
        "id": pack.id,
        "country_id": pack.country_id,
        "version": pack.version,
        "status": pack.status,
        "pack": validated.model_dump(),
        "note": pack.note,
        "author_claim": pack.author_claim,
        "approved_by": pack.approved_by,
        "approved_at": pack.approved_at.isoformat() if pack.approved_at else None,
        "created_at": pack.created_at.isoformat() if pack.created_at else None,
        "completeness": completeness(validated),
        "reference_example": validated.reference_example,
    }


def conversion_factor(pack: Pack, from_unit: str, to_unit: str) -> Optional[float]:
    """The factor from one unit to another, from the pack only. None means: ask."""
    source, target = (from_unit or "").strip().lower(), (to_unit or "").strip().lower()
    if not source or source == target:
        return 1.0
    for conversion in pack.products_units.conversions:
        if conversion.from_unit.lower() == source and conversion.to_unit.lower() == target:
            return conversion.factor
    return None
