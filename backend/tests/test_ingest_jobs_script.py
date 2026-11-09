"""The ingest CLI offline: files in tmp_path only, the database disabled or faked. No network, no real listings."""

import json

import pytest

from scripts import ingest_jobs as script

APPLY = "https://careers.example.com/apply/secret-route"
POSTER = "Synthetic Recruiter Person"
URL = "postgresql+psycopg://ingestuser:s3cret-pass@db.example.invalid:5432/jobsdb"


def record(n=1, **fields):
    base = {
        "id": f"40000000{n:02d}", "title": "Backend Developer", "companyName": "Fictional Co", "location": "Pune, India",
        "link": f"https://www.linkedin.com/jobs/view/40000000{n:02d}?trackingId=secret-tracking",
        "descriptionText": "Django and PostgreSQL.\nWrite to hr@fictional.example", "applyUrl": APPLY, "posterFullName": POSTER,
    }
    base.update(fields)
    return base


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.delenv("DATABASE_ENABLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(script, "Database", lambda *_: pytest.fail("no database object may be created"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", URL)


def write(tmp_path, data, name="export.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_run_requires_the_confirmation_flag_before_reading_anything(disabled, tmp_path, capsys):
    assert script.main(["--run", str(tmp_path / "absent.json")]) == 1
    assert "--confirm-ingest" in capsys.readouterr().out


def test_exactly_one_action_is_required(tmp_path):
    with pytest.raises(SystemExit):
        script.main([str(tmp_path / "x.json")])
    with pytest.raises(SystemExit):
        script.main(["--check", "--run", str(tmp_path / "x.json")])


@pytest.mark.parametrize("content, message", [
    (None, "does not exist"), (b"{not json", "not valid UTF-8 JSON"), (b"\xff\xfe", "not valid UTF-8 JSON"),
    (b'"a string"', "expected a JSON array"), (b'{"rows": []}', "expected a JSON array"),
])
def test_bad_input_files_fail_before_any_database_work(disabled, tmp_path, capsys, content, message):
    path = tmp_path / "export.json"
    if content is not None:
        path.write_bytes(content)
    assert script.main(["--check", str(path)]) == 1
    assert message in capsys.readouterr().out


def test_an_oversized_file_is_refused(disabled, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(script, "MAX_FILE_BYTES", 10)
    assert script.main(["--check", str(write(tmp_path, [record()]))]) == 1
    assert "larger than" in capsys.readouterr().out


@pytest.mark.parametrize("wrap", [lambda r: r, lambda r: {"items": r}, lambda r: {"data": r}])
def test_supported_top_level_shapes(disabled, tmp_path, capsys, wrap):
    assert script.main(["--check", str(write(tmp_path, wrap([record()])))]) == 0
    assert "Accepted: 1" in capsys.readouterr().out


def test_check_prints_key_names_and_counts_but_never_values_urls_or_people(disabled, tmp_path, capsys):
    path = write(tmp_path, [record(1), record(2, title="Chef"), record(3, link="http://x.example/jobs/view/3")])
    assert script.main(["--check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "applyUrl x3" in out and "posterFullName x3" in out and "link x3" in out
    assert "Accepted: 1" in out and "missing_https_source_url=1" in out and "unrecognized_role=1" in out
    assert "Backend Developer — Fictional Co -> backend-developer / pune / 2 skills" in out
    for secret in (APPLY, POSTER, "hr@fictional.example", "secret-tracking", "linkedin.com"):
        assert secret not in out, secret
    assert "mapping only; database comparison skipped" in out


def test_more_than_fifty_accepted_listings_are_refused(disabled, tmp_path, capsys):
    path = write(tmp_path, [record(n) for n in range(1, 52)])
    assert script.main(["--check", str(path)]) == 1
    assert script.main(["--run", "--confirm-ingest", str(path)]) == 1
    assert "at most 50 may be imported" in capsys.readouterr().out


def test_exactly_fifty_listings_pass_the_cap(disabled, tmp_path, capsys):
    assert script.main(["--check", str(write(tmp_path, [record(n) for n in range(1, 51)]))]) == 0
    assert "Accepted: 50" in capsys.readouterr().out


def test_nothing_accepted_is_a_report_for_check_and_a_refusal_for_run(disabled, tmp_path, capsys):
    path = write(tmp_path, [record(title="Chef")])
    assert script.main(["--check", str(path)]) == 0
    assert script.main(["--run", "--confirm-ingest", str(path)]) == 1
    assert "Nothing to import" in capsys.readouterr().out


def test_run_with_the_database_disabled_is_refused(disabled, tmp_path, capsys):
    assert script.main(["--run", "--confirm-ingest", str(write(tmp_path, [record()]))]) == 1
    assert "DATABASE_ENABLED must be 1" in capsys.readouterr().out


class FailingDatabase:
    def __init__(self, *_):
        self.closed = False

    async def check_connection(self):
        raise OSError("could not connect to db.example.invalid with password s3cret-pass")

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("argv", [["--check"], ["--run", "--confirm-ingest"]])
def test_database_failures_print_the_stage_and_class_but_never_credentials(enabled, tmp_path, capsys, monkeypatch, argv):
    monkeypatch.setattr(script, "Database", FailingDatabase)
    assert script.main([*argv, str(write(tmp_path, [record()]))]) == 2
    out = capsys.readouterr().out
    assert "host=db.example.invalid port=5432 database=jobsdb" in out
    assert "during connecting: OSError" in out and "s3cret-pass" not in out and "ingestuser" not in out
