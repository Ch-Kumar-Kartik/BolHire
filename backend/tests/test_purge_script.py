import pytest

from app.db.auth_repository import PurgeResult
from scripts import purge_expired_candidates as script

URL = "postgresql+psycopg://user:s3cret-pass@db.example.invalid:5432/app"


def configure(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", URL)


def test_check_validates_without_connecting(monkeypatch, capsys):
    configure(monkeypatch)

    async def forbidden(*_args):
        raise AssertionError("--check must not connect")

    monkeypatch.setattr(script, "_purge", forbidden)
    assert script.main(["--check"]) == 0
    printed = capsys.readouterr().out
    assert "No connection made." in printed and "s3cret-pass" not in printed


def test_run_requires_confirmation_and_enabled_database(monkeypatch, capsys):
    configure(monkeypatch)
    assert script.main(["--run"]) == 1 and "--confirm-delete" in capsys.readouterr().out
    monkeypatch.setenv("DATABASE_ENABLED", "0")
    assert script.main(["--check"]) == 1 and "DATABASE_ENABLED" in capsys.readouterr().out
    configure(monkeypatch)
    assert script.main(["--check", "--batch-size", "0"]) == 1


def test_run_reports_counts_only(monkeypatch, capsys):
    configure(monkeypatch)
    seen = []

    async def fake(settings, batch_size):
        seen.append(batch_size)
        return PurgeResult(candidates=3, sessions=5)

    monkeypatch.setattr(script, "_purge", fake)
    assert script.main(["--run", "--confirm-delete", "--batch-size", "50"]) == 0
    assert seen == [50] and "Removed 3 expired identities and 5 expired sessions." in capsys.readouterr().out


def test_unexpected_failure_exits_nonzero_and_names_only_the_class(monkeypatch, capsys):
    configure(monkeypatch)

    async def failing(*_args):
        raise RuntimeError(f"connect to {URL} failed")

    monkeypatch.setattr(script, "_purge", failing)
    assert script.main(["--run", "--confirm-delete"]) == 2
    printed = capsys.readouterr()
    text = printed.out + printed.err
    assert "RuntimeError" in text and "s3cret-pass" not in text and "db.example.invalid" not in text and "failed" not in text


class FakeDatabase:
    closed = 0

    def __init__(self, settings, connect_error=None):
        self.connect_error, self.sessions = connect_error, object()

    async def check_connection(self):
        if self.connect_error:
            raise self.connect_error

    async def close(self):
        FakeDatabase.closed += 1


@pytest.mark.parametrize("stage, connect_error, purge_error", [
    ("connecting", RuntimeError(f"could not connect to {URL}"), None),
    ("purging", None, ValueError(f"deadlock detected for {URL} user s3cret-pass")),
])
def test_failures_report_the_stage_and_class_but_never_the_message(monkeypatch, capsys, stage, connect_error, purge_error):
    configure(monkeypatch)
    FakeDatabase.closed = 0
    monkeypatch.setattr(script, "Database", lambda settings: FakeDatabase(settings, connect_error))

    class FakeRepository:
        def __init__(self, sessions):
            pass

        async def purge_expired(self, **_):
            raise purge_error

    monkeypatch.setattr(script, "AuthRepository", FakeRepository)
    assert script.main(["--run", "--confirm-delete"]) == 2
    printed = capsys.readouterr()
    text = printed.out + printed.err
    assert f"during {stage}" in text and type(connect_error or purge_error).__name__ in text
    for secret in ("s3cret-pass", "db.example.invalid", "deadlock", "could not connect"):
        assert secret not in text
    assert FakeDatabase.closed == 1  # the engine is disposed on every failure path
