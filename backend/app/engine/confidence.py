"""The confidence budget, in words.

A result carries two things about the estimates it was solved on: how much of the
demand they are, and what the same scenario looks like with every estimated figure a
swing lower and higher. This module turns that into the sentence a decision-maker
needs -- "this holds even if our demand guess is a third off" -- or, just as
usefully, the sentence that says it does not, and what changes.

The comparison against today's network is made end to end: the option at -30% is
set against the baseline at -30%, not against the baseline as modelled, because the
baseline rests on the same estimates.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_SWING = 0.30

#: The figures whose range is worth printing beside the answer.
RANGE_KEYS = ("total_cost", "fill_rate", "worst_stratum_fill_rate", "mean_stockout_risk", "hubs_open")


def swing_for(country) -> float:
    """How far to push the estimates, as a fraction. A country can carry its own."""
    config = getattr(country, "config", None) or {}
    try:
        swing = float((config.get("estimators") or {}).get("swing") or DEFAULT_SWING)
    except (TypeError, ValueError):
        swing = DEFAULT_SWING
    return min(1.0, max(0.05, swing))


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _direction(baseline_kpi: dict, kpi: dict, key: str, better: str) -> str:
    here, there = kpi.get(key), baseline_kpi.get(key)
    if not isinstance(here, (int, float)) or not isinstance(there, (int, float)):
        return "flat"
    delta = here - there
    if abs(delta) <= 1e-9 or (there and abs(delta / there) < 0.001):
        return "flat"
    if better == "lower":
        return "better" if delta < 0 else "worse"
    return "better" if delta > 0 else "worse"


def assess(result, baseline_result=None) -> dict:
    """What the estimates mean for this result, as data and as a sentence.

    ``baseline_result`` is today's network, when this result is an option against it.
    """
    conf = result.confidence or {}
    estimated = conf.get("estimated") or {}
    sensitivity = conf.get("sensitivity")
    share = float(estimated.get("demand_share_m3") or 0.0)
    kpi = result.kpi_set or {}

    out = {
        "share": share,
        "share_rows": float(estimated.get("demand_share_rows") or 0.0),
        "facilities_with_estimated_demand": int(estimated.get("facilities_with_estimated_demand") or 0),
        "facilities": int(estimated.get("facilities") or 0),
        "facilities_with_estimated_storage": int(estimated.get("facilities_with_estimated_storage") or 0),
        "tested": bool(sensitivity),
        "swing": sensitivity.get("swing") if sensitivity else None,
        "holds": None,
        "changes": [],
        "notes": [],
        "range": {},
        "baseline_tested": None,
        "sentence": "",
    }

    if share <= 0:
        out["holds"] = True
        out["sentence"] = "Nothing in this plan's demand is estimated; every figure was recorded or typed."
        return out

    opening = f"{_pct(share)} of the demand this rests on is estimated"
    if not sensitivity:
        out["sentence"] = f"{opening}. Run the scenario again to test the answer against that."
        return out

    swing = float(sensitivity.get("swing") or DEFAULT_SWING)
    low, high = sensitivity.get("low") or {}, sensitivity.get("high") or {}
    ends = (("low", low, "lower"), ("high", high, "higher"))
    changes: list[str] = []

    # Can the plan still be delivered?
    for _, end, word in ends:
        if end.get("status") != "ok":
            changes.append(
                f"With estimated demand {_pct(swing)} {word}, the plan's constraints can no longer be met."
            )

    # Does it still build the same network?
    base_hubs = set((result.solver_log or {}).get("hubs_open_codes") or [])
    for _, end, word in ends:
        if end.get("status") != "ok":
            continue
        hubs = set(end.get("hubs_open_codes") or [])
        opens, closes = sorted(hubs - base_hubs), sorted(base_hubs - hubs)
        if opens or closes:
            parts = []
            if opens:
                parts.append(f"open {', '.join(opens)}")
            if closes:
                parts.append(f"close {', '.join(closes)}")
            changes.append(f"With estimated demand {_pct(swing)} {word}, the plan would {' and '.join(parts)}.")

    # Is it still the better option, end against end?
    comparison_words: list[str] = []
    if baseline_result is not None and baseline_result is not result:
        base_sens = (baseline_result.confidence or {}).get("sensitivity") or {}
        out["baseline_tested"] = bool(base_sens)
        base_kpi = baseline_result.kpi_set or {}
        base_dir = {
            "total_cost": _direction(base_kpi, kpi, "total_cost", "lower"),
            "worst_stratum_fill_rate": _direction(base_kpi, kpi, "worst_stratum_fill_rate", "higher"),
        }
        for key, end, word in ends:
            base_end = base_sens.get(key) or {}
            if end.get("status") != "ok" or base_end.get("status") != "ok":
                continue
            end_dir = {
                "total_cost": _direction(base_end.get("kpi_set") or {}, end.get("kpi_set") or {}, "total_cost", "lower"),
                "worst_stratum_fill_rate": _direction(
                    base_end.get("kpi_set") or {}, end.get("kpi_set") or {}, "worst_stratum_fill_rate", "higher"
                ),
            }
            if base_dir["total_cost"] != "flat" and end_dir["total_cost"] not in ("flat", base_dir["total_cost"]):
                changes.append(
                    f"With estimated demand {_pct(swing)} {word}, this option costs "
                    f"{'more' if end_dir['total_cost'] == 'worse' else 'less'} than today's network, "
                    f"reversing the comparison."
                )
            if base_dir["worst_stratum_fill_rate"] != "flat" and end_dir["worst_stratum_fill_rate"] not in (
                "flat",
                base_dir["worst_stratum_fill_rate"],
            ):
                changes.append(
                    f"With estimated demand {_pct(swing)} {word}, supply to the most vulnerable fifth "
                    f"{'falls below' if end_dir['worst_stratum_fill_rate'] == 'worse' else 'rises above'} "
                    f"today's, reversing the comparison."
                )
        if base_dir["total_cost"] != "flat" and out["baseline_tested"]:
            comparison_words.append(
                "it stays cheaper than today at both ends"
                if base_dir["total_cost"] == "better"
                else "it stays dearer than today at both ends"
            )

    # Not a reversal, but worth a sentence: the network runs short at the high end.
    notes: list[str] = []
    base_fill = kpi.get("fill_rate")
    for _, end, word in ends:
        fill = (end.get("kpi_set") or {}).get("fill_rate") if end.get("status") == "ok" else None
        if isinstance(fill, (int, float)) and isinstance(base_fill, (int, float)) and base_fill - fill > 0.01:
            unmet = (end.get("kpi_set") or {}).get("unmet_m3")
            notes.append(
                f"With estimated demand {_pct(swing)} {word}, {_pct(1 - fill)} of demand goes unmet"
                f"{f' ({unmet:,.0f} m³ a year)' if isinstance(unmet, (int, float)) else ''}, "
                f"against {_pct(1 - base_fill)} as modelled."
            )
    out["notes"] = notes

    # The ranges, low to high, with the modelled figure between.
    for key in RANGE_KEYS:
        values = [
            (low.get("kpi_set") or {}).get(key) if low.get("status") == "ok" else None,
            kpi.get(key),
            (high.get("kpi_set") or {}).get(key) if high.get("status") == "ok" else None,
        ]
        if kpi.get(key) is not None:
            out["range"][key] = values

    out["changes"] = changes
    out["holds"] = not changes
    if changes:
        more = f" And {len(changes) - 1} more." if len(changes) > 1 else ""
        out["sentence"] = f"{opening}, and the answer depends on it. {changes[0]}{more}"
    else:
        hub_words = (
            f"the same {len(base_hubs)} {'store stays' if len(base_hubs) == 1 else 'stores stay'} open"
            if base_hubs
            else "the plan is unchanged"
        )
        tail = ", and ".join([hub_words] + comparison_words)
        out["sentence"] = (
            f"{opening}. The answer holds with every estimated figure {_pct(swing)} lower or higher: {tail}."
        )
    return out
