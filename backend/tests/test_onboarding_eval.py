"""The evaluation set is a test: the PNG data degraded on purpose must onboard to the
PRD's targets with the rules alone, or the build has regressed."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Country
from app.onboarding import evaluate


def test_the_degraded_png_set_meets_the_targets_with_rules_only(isolated_session_factory):
    session = isolated_session_factory()
    try:
        country = session.scalar(select(Country).where(Country.code == "PNG"))
        scores = evaluate.run(session, country, seed=7)
        assert scores["facilities"]["auto_precision"] >= 0.98, scores["facilities"]["wrong_auto_accepts"]
        assert scores["facilities"]["auto_recall"] >= 0.9
        assert scores["conflicts"]["silently_resolved"] == 0 and scores["conflicts"]["surfaced"] == scores["conflicts"]["planted"]
        assert scores["anomalies"]["recall"] >= 0.9, scores["anomalies"]
        assert scores["provenance"]["wrong_class"] == 0
        assert scores["provenance"]["totals"]["illustrative"] == 0
        assert scores["mapping"]["precision"] >= 0.85, scores["mapping"]["wrong"]
        assert scores["estimates"]["unfillable"] == 0
        assert scores["passes"] is True, scores["targets"]
    finally:
        session.rollback()
        session.close()
