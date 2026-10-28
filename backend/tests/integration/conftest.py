"""All destructive setup is restricted to a unique schema in a local test DB."""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


def migration(connection, revision, *, downgrade=False):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.fixture
def db_url():
    raw = os.getenv("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL is not set; local PostgreSQL integration requires explicit opt-in.")
    try:
        url = make_url(raw)
        valid = (url.drivername == "postgresql+psycopg" and url.host in {"127.0.0.1", "localhost", "::1"}
                 and url.database and url.database.endswith("_test") and not url.query)
    except Exception:
        valid = False
    if not valid:
        pytest.fail("Integration tests require a local psycopg URL, a database ending in _test, and no URL options.")
    schema = "bolhire_test_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with admin.begin() as connection:
            assert connection.scalar(text("SHOW server_version_num")).startswith("17"), "PostgreSQL 17 is required"
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
        engine = create_engine(scoped, poolclass=NullPool, hide_parameters=True)
        try:
            with engine.begin() as connection:
                migration(connection, "head")
            yield scoped
        finally:
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        admin.dispose()
