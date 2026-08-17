"""Application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .api import exports, ingest, network, scenarios
from .config import settings
from .db import Base, SessionLocal, engine
from .engine.runner import run_scenario
from .models import Result, Scenario
from .seed.loader import seed_png

logger = logging.getLogger("hscn")

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    if settings.seed_on_startup:
        session = SessionLocal()
        try:
            country = seed_png(session)
            _ensure_baseline_result(session, country)
            logger.info("PNG workspace ready (country id %s).", country.id)
        finally:
            session.close()
    yield


def _ensure_baseline_result(session, country) -> None:
    """Run the baseline once so the application opens with a live map.

    Only the baseline: running every scenario here would spend the demo's best
    moment -- a scenario set solving in parallel while the room watches -- before
    anybody is in the room.
    """
    baseline = session.scalar(
        select(Scenario).where(Scenario.country_id == country.id, Scenario.is_baseline.is_(True))
    )
    if not baseline:
        return
    already = session.scalar(select(Result).where(Result.scenario_id == baseline.id))
    if already:
        return
    try:
        run_scenario(session, baseline)
    except Exception:  # noqa: BLE001 - a failed warm-up must never block startup
        logger.exception("Baseline warm-up run failed; the app will start without it.")


app = FastAPI(
    title="Health Supply Chain Network Design",
    version="0.1.0",
    description=(
        "A living digital twin of a national health supply chain that models sea, air and "
        "seasonal road access as first-class realities, scores every scenario on cost and "
        "equity, and stays with the government after the consultant leaves."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(network.router, prefix="/api")
app.include_router(scenarios.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(exports.router, prefix="/api")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "solver": "HiGHS",
        "osrm_configured": bool(settings.osrm_url),
        "database": settings.database_url.split("://", 1)[0],
    }


# Serve the built frontend when it exists, so the whole application is one process
# in a field deployment. In development the Vite dev server proxies to this API instead.
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
