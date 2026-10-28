import asyncio

import pytest

from app.db.config import DatabaseSettings
from app.db.database import DatabaseUnavailableError
from app.main import create_app


def test_database_disabled_ignores_incidental_url():
    assert DatabaseSettings.from_env({}) == DatabaseSettings()
    assert DatabaseSettings.from_env({"DATABASE_URL": "invalid"}) == DatabaseSettings()


@pytest.mark.parametrize("env", [
    {"DATABASE_ENABLED": "true"}, {"DATABASE_ENABLED": "1"},
    {"DATABASE_ENABLED": "1", "DATABASE_URL": "sqlite:///secret"},
    {"DATABASE_ENABLED": "1", "DATABASE_URL": "postgresql+psycopg:///missing-host"},
])
def test_invalid_database_configuration_is_safe(env):
    with pytest.raises(ValueError) as error:
        DatabaseSettings.from_env(env)
    assert "secret" not in str(error.value)


def test_settings_repr_hides_url():
    settings = DatabaseSettings.from_env({"DATABASE_ENABLED": "1", "DATABASE_URL":
        "postgresql+psycopg://user:private@localhost/db"})
    assert settings.enabled and "private" not in repr(settings)


@pytest.mark.parametrize("enabled,fail", [(False, False), (True, False), (True, True)])
def test_application_database_lifecycle(monkeypatch, enabled, fail):
    monkeypatch.setenv("DATABASE_ENABLED", "1" if enabled else "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/test")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("SPEECH_PROVIDER", "mock")
    calls = []

    class FakeDatabase:
        def __init__(self, settings):
            calls.append("create")

        async def check_connection(self):
            calls.append("check")
            if fail:
                raise DatabaseUnavailableError()

        async def close(self):
            calls.append("close")

    monkeypatch.setattr("app.main.Database", FakeDatabase)
    app = create_app()

    async def scenario():
        async with app.router.lifespan_context(app):
            assert (app.state.database is not None) == enabled

    if fail:
        with pytest.raises(DatabaseUnavailableError):
            asyncio.run(scenario())
    else:
        asyncio.run(scenario())
    assert calls == (["create", "check", "close"] if enabled else [])
    assert app.state.database is None
