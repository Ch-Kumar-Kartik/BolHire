"""Import REAL job listings from an Apify LinkedIn jobs dataset JSON export (local demo only, at most 50 listings).

    python -m scripts.ingest_jobs --check <file>                    map offline and report; compare read-only if the DB is enabled
    python -m scripts.ingest_jobs --run --confirm-ingest <file>     map, then write (idempotent; never deletes or retires)

Keep the export under `local_data/` (outside version control). Never commit or deploy it. The file is read locally; nothing is
fetched from the network. Apply links, apply methods and job-poster, recruiter or applicant data are never read or stored. The
report prints key NAMES and job fields only (title, company, resolved keys), never raw values, URLs or credentials. The target
host, port and database name are printed before any database work; the password never is.
"""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.db.config import DatabaseSettings
from app.db.database import Database
from app.db.job_catalogue_seed import JobsTableMissingError
from app.db.job_import import ImportCounts, ImportPlan, JobsSchemaOutdatedError, check_import, import_jobs
from app.jobs.linkedin_import import ImportReport, ImportedJob, normalize_records

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_LISTINGS = 50


class IngestStageError(Exception):
    """Carries only the failing stage and the exception class name: driver messages can contain hosts, users, or SQL."""

    def __init__(self, stage: str, cause: str) -> None:
        super().__init__(f"{stage}: {cause}")
        self.stage = stage
        self.cause = cause


class InputError(Exception):
    pass


def _printable(text: str, limit: int = 80) -> str:
    cleaned = "".join(ch if ch.isprintable() else "?" for ch in text)
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def load_records(path: Path) -> list[object]:
    if not path.is_file():
        raise InputError("the input file does not exist")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise InputError(f"the input file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise InputError("the input file is not valid UTF-8 JSON") from None
    if isinstance(data, dict):
        data = next((data[key] for key in ("items", "data") if isinstance(data.get(key), list)), None)
    if not isinstance(data, list):
        raise InputError("expected a JSON array of records (or an object with an 'items' or 'data' array)")
    return data


def describe(report: ImportReport) -> list[str]:
    lines = [f"Records in file: {report.records_seen}"]
    lines.append(f"Keys found ({len(report.key_counts)} distinct, name x records):")
    lines.extend(f"  {_printable(name)} x{count}" for name, count in report.key_counts.items())
    lines.append(f"Accepted: {len(report.accepted)}")
    dropped = sum(report.dropped.values())
    lines.append(f"Dropped: {dropped}" + ("" if not dropped else " (" + ", ".join(f"{k}={v}" for k, v in report.dropped.items()) + ")"))
    lines.append("Defaults used: " + ", ".join(f"{k}={v}" for k, v in report.defaults.items()))
    lines.append("Ambiguous picks (first in vocabulary order): " + ", ".join(f"{k}={v}" for k, v in report.ambiguous.items()))
    lines.append("Seniority: " + ", ".join(f"{k}={v}" for k, v in report.seniority.items()))
    lines.append(f"retrieved_at taken from the file's modification time: {report.retrieved_at_fallbacks}")
    for job in report.accepted:
        lines.append(f"  + {_printable(job.title, 60)} — {_printable(job.company, 40)} -> {job.role_key} / {job.city_key or job.work_mode}"
                     f" / {len(job.required_skills)} skills")
    return lines


def _target(settings: DatabaseSettings) -> str:
    url = make_url(settings.url)
    return f"host={url.host} port={url.port} database={url.database}"


async def _with_database(settings: DatabaseSettings, stage: str, work):
    database = Database(settings)
    try:
        try:
            await database.check_connection()
        except Exception as error:  # noqa: BLE001 - reduced to its class name on purpose
            raise IngestStageError("connecting", type(error).__name__) from None
        try:
            return await work(database.engine)
        except (JobsTableMissingError, JobsSchemaOutdatedError):
            raise
        except Exception as error:  # noqa: BLE001
            raise IngestStageError(stage, type(error).__name__) from None
    finally:
        await database.close()


def _counts(prefix: str, counts: ImportCounts) -> str:
    return f"{prefix}: {counts.inserted} inserted, {counts.updated} updated, {counts.unchanged} unchanged (nothing is ever retired)."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import real job listings from a local Apify LinkedIn export (demo only).")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Map and report; compare read-only when the database is enabled.")
    action.add_argument("--run", action="store_true", help="Write the accepted listings to the database.")
    parser.add_argument("--confirm-ingest", action="store_true", help="Required with --run.")
    parser.add_argument("file", type=Path, help="Path to the exported JSON file (keep it under local_data/).")
    args = parser.parse_args(argv)

    if args.run and not args.confirm_ingest:
        print("Preflight FAIL: --run requires --confirm-ingest.")
        return 1
    try:
        records = load_records(args.file)
        retrieved_fallback = datetime.fromtimestamp(args.file.stat().st_mtime, tz=UTC)
    except InputError as error:
        print(f"Input FAIL: {error}.")
        return 1
    report = normalize_records(records, retrieved_fallback=retrieved_fallback, now=datetime.now(UTC))
    for line in describe(report):
        print(line)
    accepted: tuple[ImportedJob, ...] = report.accepted
    if len(accepted) > MAX_LISTINGS:
        print(f"Input FAIL: {len(accepted)} listings were accepted; at most {MAX_LISTINGS} may be imported. Trim the file.")
        return 1
    if not accepted:
        print("Nothing to import: no record passed the checks.")
        return 0 if args.check else 1

    try:
        settings = DatabaseSettings.from_env(os.environ)
    except ValueError as error:
        print(f"Preflight FAIL: {error}")
        return 1
    if not settings.enabled:
        if args.check:
            print("Database: mapping only; database comparison skipped (DATABASE_ENABLED is not 1).")
            return 0
        print("Preflight FAIL: DATABASE_ENABLED must be 1 to import.")
        return 1

    print(f"Target: {_target(settings)}")
    try:
        if args.check:
            plan: ImportPlan = asyncio.run(_with_database(settings, "reading", lambda engine: check_import(engine, accepted)))
            print(_counts("Plan (nothing was written)", plan.counts()))
            return 0
        _, counts = asyncio.run(_with_database(settings, "importing", lambda engine: import_jobs(engine, accepted)))
    except JobsTableMissingError:
        print("The jobs table does not exist: run `alembic upgrade head` against this database first.")
        return 2
    except JobsSchemaOutdatedError:
        print("The jobs table has no provenance columns: run `alembic upgrade head` (migration 0003) against this database first.")
        return 2
    except IngestStageError as error:
        print(f"Import FAIL during {error.stage}: {error.cause}. The error message is withheld because it can contain credentials or hosts.")
        return 2
    except Exception as error:  # noqa: BLE001
        print(f"Import FAIL: unexpected {type(error).__name__}. The error message is withheld.")
        return 2
    print(_counts("Imported", counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
