from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from apex.db.migrate import upgrade
from apex.db.session import make_engine, make_session_factory

PG_URL = os.environ.get("APEX_TEST_POSTGRES_URL")  # e.g. postgresql+psycopg://apex:apex@localhost/apex_test

BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])


def pytest_configure(config):
    # Code that pandas has announced it will remove fails the tests now, not on the day a new
    # pandas comes out (APEX does not cap the pandas version, so a fresh install gets the latest).
    import pandas.errors

    if hasattr(pandas.errors, "Pandas4Warning"):  # pandas 3.x
        config.addinivalue_line("filterwarnings", "error::pandas.errors.Pandas4Warning")


@pytest.fixture(params=BACKENDS)
def db_url(request, tmp_path):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'apex.db'}"
    else:
        url = PG_URL
        eng = make_engine(url)
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        eng.dispose()
    upgrade(url)
    return url


@pytest.fixture
def session(db_url):
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)
