"""Equity as an optimisation objective, not a footnote.

No tool in the competitive set treats equity as anything a model can be asked to
respect. That is not an oversight in those tools -- they were built for commercial
networks where cost, service and risk are the whole story. In a health system it is
politically unacceptable, and everyone in a UNICEF room knows it: a 15% saving that
comes entirely out of the hardest-to-reach 10% of the population is not a saving,
it is a transfer.

So equity here is three concrete things:

1. A **vulnerability index** per facility, computed from network structure alone
   (remoteness, terrain, months of restricted access, mode dependency), so it is
   known before any optimisation runs and cannot be gamed by the solution.
2. Population-weighted **strata** (quintiles) of that index, which is what gets
   reported. "Who pays for the savings" is a comparison of fill rate by stratum
   against the baseline.
3. An **equity floor**: a hard linear constraint that every stratum must reach a
   minimum fill rate. This is the lever that turns the demo's cost saving from 15%
   into 9% and holds the bottom quintile flat.
"""

from __future__ import annotations

from dataclasses import dataclass

from .geo import haversine_km

#: How exposed a facility is by virtue of the terrain it sits in.
TERRAIN_VULNERABILITY: dict[str, float] = {
    "mainland_road": 0.10,
    "coastal_road": 0.30,
    "highlands_road": 0.55,
    "riverine": 0.70,
    "island": 0.75,
    "remote_air_only": 1.00,
}

#: Mode dependency: a facility reachable only by air or boat is structurally fragile.
MODE_VULNERABILITY: dict[str, float] = {
    "road": 0.0,
    "river": 0.5,
    "sea": 0.6,
    "air": 0.9,
    "foot": 0.8,
    "drone": 0.7,
}

WEIGHTS = {
    "remoteness": 0.35,
    "terrain": 0.25,
    "seasonal_isolation": 0.25,
    "mode_dependency": 0.15,
}


@dataclass
class NodeVulnerability:
    node_code: str
    score: float
    remoteness: float
    terrain: float
    seasonal_isolation: float
    mode_dependency: float
    nearest_hub_hours: float
    restricted_months: int
    population: float

    def as_dict(self) -> dict:
        return {
            "node_code": self.node_code,
            "vulnerability": round(self.score, 4),
            "components": {
                "remoteness": round(self.remoteness, 3),
                "terrain": round(self.terrain, 3),
                "seasonal_isolation": round(self.seasonal_isolation, 3),
                "mode_dependency": round(self.mode_dependency, 3),
            },
            "nearest_hub_hours": round(self.nearest_hub_hours, 2),
            "restricted_months": self.restricted_months,
            "population": self.population,
        }


def compute_vulnerability(
    facilities: list,
    hubs: list,
    edges_by_facility: dict[int, list],
) -> dict[int, NodeVulnerability]:
    """Vulnerability index in [0, 1] for each facility.

    Structural only: it depends on where a facility is and what can reach it, not on
    what any scenario decided to do. That matters -- an index that moved with the
    solution could be optimised against instead of respected.
    """
    hub_points = [(h.lat, h.lon) for h in hubs] or [(f.lat, f.lon) for f in facilities[:1]]

    raw_hours: dict[int, float] = {}
    for facility in facilities:
        lanes = edges_by_facility.get(facility.id, [])
        if lanes:
            raw_hours[facility.id] = min(e.base_travel_time_hr for e in lanes)
        else:
            # No lane at all: fall back to straight-line distance at a pessimistic speed.
            nearest_km = min(haversine_km(facility.lat, facility.lon, hlat, hlon) for hlat, hlon in hub_points)
            raw_hours[facility.id] = nearest_km / 25.0

    max_hours = max(raw_hours.values()) if raw_hours else 1.0
    max_hours = max(max_hours, 1e-6)

    out: dict[int, NodeVulnerability] = {}
    for facility in facilities:
        lanes = edges_by_facility.get(facility.id, [])

        remoteness = min(1.0, raw_hours[facility.id] / max_hours)
        terrain = TERRAIN_VULNERABILITY.get(facility.terrain_class, 0.5)

        # Seasonal isolation: months in which *every* lane serving this facility is
        # degraded below half throughput. One good all-weather road is enough to
        # score zero here, which is the right incentive.
        restricted = 0
        if lanes:
            for month_index in range(12):
                best = max((e.monthly_access or [1.0] * 12)[month_index] for e in lanes)
                if best < 0.5:
                    restricted += 1
        else:
            restricted = 12
        seasonal_isolation = restricted / 12.0

        mode_dependency = (
            min(MODE_VULNERABILITY.get(e.mode, 0.5) for e in lanes) if lanes else 1.0
        )

        score = (
            WEIGHTS["remoteness"] * remoteness
            + WEIGHTS["terrain"] * terrain
            + WEIGHTS["seasonal_isolation"] * seasonal_isolation
            + WEIGHTS["mode_dependency"] * mode_dependency
        )

        out[facility.id] = NodeVulnerability(
            node_code=facility.code,
            score=round(min(1.0, score), 4),
            remoteness=remoteness,
            terrain=terrain,
            seasonal_isolation=seasonal_isolation,
            mode_dependency=mode_dependency,
            nearest_hub_hours=raw_hours[facility.id],
            restricted_months=restricted,
            population=float(facility.catchment_population or 0.0),
        )
    return out


STRATUM_LABELS = [
    "Q1 least vulnerable",
    "Q2",
    "Q3",
    "Q4",
    "Q5 most vulnerable",
]


def assign_strata(vulnerability: dict[int, NodeVulnerability], n_strata: int = 5) -> dict[int, int]:
    """Population-weighted quantiles of the vulnerability index.

    Weighted by population rather than facility count, so a stratum is "a fifth of
    the people", not "a fifth of the buildings". Ministries care about the former.
    """
    if not vulnerability:
        return {}

    ordered = sorted(vulnerability.values(), key=lambda v: v.score)
    total_pop = sum(v.population for v in ordered)
    if total_pop <= 0:
        # No population data: fall back to equal counts so the panel still works.
        chunk = max(1, len(ordered) // n_strata)
        return {
            _find_id(vulnerability, v): min(n_strata - 1, i // chunk) for i, v in enumerate(ordered)
        }

    strata: dict[int, int] = {}
    cumulative = 0.0
    for vuln in ordered:
        share = cumulative / total_pop
        index = min(n_strata - 1, int(share * n_strata))
        strata[_find_id(vulnerability, vuln)] = index
        cumulative += vuln.population
    return strata


def _find_id(vulnerability: dict[int, NodeVulnerability], target: NodeVulnerability) -> int:
    for node_id, value in vulnerability.items():
        if value is target:
            return node_id
    raise KeyError(target.node_code)


def equity_report(
    strata: dict[int, int],
    vulnerability: dict[int, NodeVulnerability],
    served: dict[int, float],
    demanded: dict[int, float],
    cost_by_node: dict[int, float],
    n_strata: int = 5,
) -> dict:
    """Fill rate, population and cost per head, by vulnerability stratum."""
    buckets: list[dict] = [
        {
            "index": i,
            "label": STRATUM_LABELS[i] if i < len(STRATUM_LABELS) else f"Q{i + 1}",
            "facilities": 0,
            "population": 0.0,
            "demand": 0.0,
            "served": 0.0,
            "cost": 0.0,
            "mean_vulnerability": 0.0,
        }
        for i in range(n_strata)
    ]

    for node_id, stratum in strata.items():
        bucket = buckets[stratum]
        bucket["facilities"] += 1
        bucket["population"] += vulnerability[node_id].population
        bucket["demand"] += demanded.get(node_id, 0.0)
        bucket["served"] += served.get(node_id, 0.0)
        bucket["cost"] += cost_by_node.get(node_id, 0.0)
        bucket["mean_vulnerability"] += vulnerability[node_id].score

    for bucket in buckets:
        count = max(1, bucket["facilities"])
        bucket["mean_vulnerability"] = round(bucket["mean_vulnerability"] / count, 4)
        bucket["fill_rate"] = round(bucket["served"] / bucket["demand"], 4) if bucket["demand"] else 1.0
        bucket["cost_per_capita"] = (
            round(bucket["cost"] / bucket["population"], 2) if bucket["population"] else 0.0
        )
        bucket["population"] = round(bucket["population"])
        bucket["demand"] = round(bucket["demand"], 1)
        bucket["served"] = round(bucket["served"], 1)
        bucket["cost"] = round(bucket["cost"], 2)

    fill_rates = [b["fill_rate"] for b in buckets if b["facilities"]]
    worst = min(fill_rates) if fill_rates else 1.0
    best = max(fill_rates) if fill_rates else 1.0

    return {
        "strata": buckets,
        "worst_stratum_fill_rate": round(worst, 4),
        "best_stratum_fill_rate": round(best, 4),
        "equity_gap": round(best - worst, 4),
        "n_strata": n_strata,
    }


def equity_penalty_weight(vulnerability_score: float, equity_weight: float) -> float:
    """Multiplier on the cost of failing to serve a facility.

    With ``equity_weight`` at 0 every unserved cubic metre is equally regrettable.
    At 1.0 an unserved cubic metre in the most vulnerable stratum counts for four
    times one in the least. This is what makes equity an *objective* rather than a
    report: it changes the solution, not just the slide.
    """
    return 1.0 + 3.0 * max(0.0, equity_weight) * max(0.0, min(1.0, vulnerability_score))
