from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import Base  # noqa: E402
from app.seed.loader import seed_png  # noqa: E402


@pytest.fixture(scope="session")
def seeded_session_factory(tmp_path_factory):
    """One seeded in-memory-ish database shared across the suite.

    Seeding runs the distance cascade over ~190 lanes, so doing it once and reusing
    the file keeps the suite fast without any test being able to see another's writes
    (each test gets its own session, and the tests that mutate use their own copies).
    """
    return _build_seeded_database(tmp_path_factory.mktemp("db") / "test.db")


def _build_seeded_database(path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    seed_png(session)
    session.close()
    return factory


@pytest.fixture(scope="module")
def isolated_session_factory(tmp_path_factory):
    """A seeded database of its own, for tests that commit changes to the model.

    The shared fixture is read-mostly and fast. Anything that applies an import or a
    sync has to be isolated, or it silently decides what every later test is looking
    at -- which is precisely the failure that a merge is supposed to prevent in the
    product, so it would be poor form to allow it in the suite.
    """
    return _build_seeded_database(tmp_path_factory.mktemp("db-isolated") / "test.db")


@pytest.fixture
def session(seeded_session_factory):
    session = seeded_session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
