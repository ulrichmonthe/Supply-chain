"""Runtime configuration.

Defaults are chosen so the app runs with zero infrastructure (SQLite on disk).
Set DATABASE_URL to a PostGIS instance for the production path.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
VAR_DIR = BACKEND_DIR / "var"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HSCN_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{VAR_DIR / 'hscn.db'}"

    # Distance cascade. When an OSRM instance is reachable it is preferred over
    # the great-circle + detour-factor fallback. Self-hosted per region; see README.
    osrm_url: str | None = None
    osrm_timeout_s: float = 5.0

    # Great-circle detour factors by terrain class, used when OSRM is unavailable.
    # These are assumptions, not measurements: every distance produced this way is
    # tagged `detour_factor` so the number can be defended (or overridden) in a workshop.
    default_detour_factor: float = 1.35

    seed_on_startup: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    max_upload_mb: int = 25


settings = Settings()
VAR_DIR.mkdir(parents=True, exist_ok=True)
