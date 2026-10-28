import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import CreateTable

import asyncio

from app.db.models import Base
from app.db.database import DatabaseUnavailableError
from app.main import create_app
from conftest import migration

pytestmark = pytest.mark.integration


def test_migrations_repeat_downgrade_upgrade_and_metadata_agree(db_url):
    engine = create_engine(db_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            migration(connection, "head")
            assert set(inspect(connection).get_table_names()) == {
                "alembic_version", "candidates", "candidate_profiles", "candidate_auth_sessions",
            }
            context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
            assert compare_metadata(context, Base.metadata) == []
            for table in Base.metadata.sorted_tables:
                checks = {c["name"]: c["sqltext"] for c in inspect(connection).get_check_constraints(table.name)}
                # Let PostgreSQL normalize ORM check expressions too. Alembic's
                # compare_metadata does not compare CHECK bodies by itself.
                ddl = str(CreateTable(table, include_foreign_key_constraints=[]).compile(dialect=connection.dialect))
                expected_name = "expected_" + table.name
                ddl = ddl.replace("CREATE TABLE " + table.name, "CREATE TEMP TABLE " + expected_name, 1)
                connection.exec_driver_sql(ddl)
                expected = {c["name"]: c["sqltext"] for c in inspect(connection).get_check_constraints(expected_name)}
                assert checks == expected
                connection.exec_driver_sql("DROP TABLE " + expected_name)
            migration(connection, "base", downgrade=True)
            assert inspect(connection).get_table_names() == ["alembic_version"]
            assert connection.scalar(text("SELECT to_regprocedure('bolhire_valid_skills(text[])')")) is None
            migration(connection, "head")
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_enabled_application_connects_without_applying_migrations(db_url, monkeypatch):
    engine = create_engine(db_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            migration(connection, "base", downgrade=True)
        monkeypatch.setenv("DATABASE_ENABLED", "1")
        monkeypatch.setenv("DATABASE_URL", db_url.render_as_string(hide_password=False))
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        monkeypatch.setenv("SPEECH_PROVIDER", "mock")
        app = create_app()

        async def scenario():
            async with app.router.lifespan_context(app):
                assert app.state.database is not None
            assert app.state.database is None

        asyncio.run(scenario())
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == ["alembic_version"]
    finally:
        engine.dispose()


def test_unavailable_local_database_fails_startup_safely(db_url, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", db_url.set(port=1).render_as_string(hide_password=False))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("SPEECH_PROVIDER", "mock")
    app = create_app()

    async def scenario():
        async with app.router.lifespan_context(app):
            pytest.fail("Unavailable database must prevent startup")

    with pytest.raises(DatabaseUnavailableError) as error:
        asyncio.run(scenario())
    assert db_url.password not in str(error.value) + caplog.text
    assert app.state.database is None
