#!/usr/bin/env python3
"""Run the onboarding evaluation on a scratch copy of the PNG reference workspace.

    .venv/bin/python scripts/onboarding_eval.py            # rules-only baseline
    .venv/bin/python scripts/onboarding_eval.py --agent    # the agent, if the pack and environment allow it
    .venv/bin/python scripts/onboarding_eval.py --seeds 1 2 3

Prints the scores the PRD asks for and exits non-zero if any target is missed. The agent
must beat the rules-only baseline on this set to ship; run both and compare.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

BACKEND = pathlib.Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
from app.models import Country  # noqa: E402
from app.onboarding import evaluate  # noqa: E402
from app.seed.loader import seed_png  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, nargs="*", default=[7])
    parser.add_argument("--agent", action="store_true", help="use the external proposer when the pack and environment allow it")
    parser.add_argument("--share", type=float, default=1.0, help="share of facilities to include")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = pathlib.Path(tempfile.mkdtemp()) / "eval.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    seed_png(session)
    country = session.scalar(select(Country).where(Country.code == "PNG"))

    results = []
    for seed in args.seeds:
        scores = evaluate.run(session, country, seed=seed, facility_share=args.share, use_agent=args.agent)
        session.commit()
        results.append(scores)
        if args.json:
            continue
        f, a, c, p = scores["facilities"], scores["anomalies"], scores["conflicts"], scores["provenance"]
        print(f"seed {seed} · proposer {scores['proposer']}")
        print(f"  mapping        {scores['mapping']['right']}/{scores['mapping']['proposed']} right (precision {scores['mapping']['precision']}, recall {scores['mapping']['recall']})")
        print(f"  facilities     {f['rows']} rows: {f['auto_accepted']} auto-accepted (precision {f['auto_precision']}, recall {f['auto_recall']}), {f['pending']} to review ({f['pending_with_right_candidate']} with the right candidate listed), {f['new']} new ({f['new_right']} truly new)")
        if f["wrong_auto_accepts"]:
            print(f"  WRONG AUTO-ACCEPTS: {f['wrong_auto_accepts']}")
        print(f"  conflicts      {c['surfaced']}/{c['planted']} planted disagreements surfaced, {c['silently_resolved']} silently resolved, {c['extra']} others found")
        print(f"  anomalies      {a['caught']}/{a['planted']} planted caught (recall {a['recall']}); {a['flags_total']} flags in all")
        print(f"  estimates      {scores['estimates']['proposed']} proposed by method {scores['estimates']['by_method']}, {scores['estimates']['unfillable']} unfillable")
        print(f"  provenance     {p['totals']} · wrong class: {p['wrong_class']}")
        print(f"  queue          {scores['queue']['total']} pending by kind {scores['queue']['by_kind']}, by confidence {scores['queue']['by_confidence']}")
        print(f"  targets        {scores['targets']} -> {'PASS' if scores['passes'] else 'FAIL'}")
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    return 0 if all(r["passes"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
