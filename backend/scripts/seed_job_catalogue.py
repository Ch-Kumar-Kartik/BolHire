"""Seed the SAMPLE job catalogue (24 listings from fictional companies; not live vacancies).

    python -m scripts.seed_job_catalogue --check                     validate offline, then compare read-only if the database is enabled
    python -m scripts.seed_job_catalogue --run --confirm-seed        validate, then write (idempotent; never deletes)

The catalogue is validated before any database access. `--check` writes nothing and takes no lock. The target host, port and
database name are printed before any database work; the password never is. Run it only against a database you intend to seed.
"""

import argparse
import asyncio
import os

from sqlalchemy.engine import make_url

from app.db.config import DatabaseSettings
from app.db.database import Database
from app.db.job_catalogue_seed import (
    CatalogueInvalidError, JobsTableMissingError, Plan, SeedCounts, check_catalogue, run_seed,
)
from app.jobs.catalogue_data import CATALOGUE
from app.jobs.catalogue_validation import validate_catalogue


class SeedStageError(Exception):
    """Carries only the failing stage and the exception class name: driver messages can contain hosts, users, or SQL."""

    def __init__(self, stage: str, cause: str) -> None:
        super().__init__(f"{stage}: {cause}")
        self.stage = stage
        self.cause = cause


def _target(settings: DatabaseSettings) -> str:
    url = make_url(settings.url)
    return f"host={url.host} port={url.port} database={url.database}"


async def _check(settings: DatabaseSettings) -> Plan:
    database = Database(settings)
    try:
        try:
            await database.check_connection()
        except Exception as error:  # noqa: BLE001 - reduced to its class name on purpose
            raise SeedStageError("connecting", type(error).__name__) from None
        try:
            return await check_catalogue(database.engine, CATALOGUE)
        except JobsTableMissingError:
            raise
        except Exception as error:  # noqa: BLE001
            raise SeedStageError("reading", type(error).__name__) from None
    finally:
        await database.close()


async def _run(settings: DatabaseSettings) -> SeedCounts:
    database = Database(settings)
    try:
        try:
            await database.check_connection()
        except Exception as error:  # noqa: BLE001
            raise SeedStageError("connecting", type(error).__name__) from None
        try:
            return (await run_seed(database.engine, CATALOGUE))[1]
        except (JobsTableMissingError, CatalogueInvalidError):
            raise
        except Exception as error:  # noqa: BLE001
            raise SeedStageError("seeding", type(error).__name__) from None
    finally:
        await database.close()


def _describe(prefix: str, counts: SeedCounts) -> str:
    return (f"{prefix}: {counts.inserted} inserted, {counts.updated} updated, {counts.reactivated} reactivated, "
            f"{counts.retired} retired, {counts.unchanged} unchanged.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and seed the sample job catalogue (fictional companies, sample listings).")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Validate the catalogue; compare read-only when the database is enabled.")
    action.add_argument("--run", action="store_true", help="Write the catalogue to the database.")
    parser.add_argument("--confirm-seed", action="store_true", help="Required with --run.")
    args = parser.parse_args(argv)

    if args.run and not args.confirm_seed:
        print("Preflight FAIL: --run requires --confirm-seed.")
        return 1
    errors = validate_catalogue(CATALOGUE)
    if errors:
        print(f"Catalogue FAIL: {len(errors)} validation errors; no database access was made.")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"Catalogue: PASS ({len(CATALOGUE)} sample jobs, fictional companies).")

    try:
        settings = DatabaseSettings.from_env(os.environ)
    except ValueError as error:
        print(f"Preflight FAIL: {error}")
        return 1
    if not settings.enabled:
        if args.check:
            print("Database: validation only; database comparison skipped (DATABASE_ENABLED is not 1).")
            return 0
        print("Preflight FAIL: DATABASE_ENABLED must be 1 for seeding.")
        return 1

    print(f"Target: {_target(settings)}")
    try:
        if args.check:
            plan = asyncio.run(_check(settings))
            print(_describe("Plan (nothing was written)", plan.counts()))
            return 0
        counts = asyncio.run(_run(settings))
    except JobsTableMissingError:
        print("The jobs table does not exist: run `alembic upgrade head` against this database first.")
        return 2
    except CatalogueInvalidError as error:
        print(f"Catalogue FAIL: {len(error.errors)} validation errors.")
        return 1
    except SeedStageError as error:
        print(f"Seed FAIL during {error.stage}: {error.cause}. The error message is withheld because it can contain credentials or hosts.")
        return 2
    except Exception as error:  # noqa: BLE001
        print(f"Seed FAIL: unexpected {type(error).__name__}. The error message is withheld.")
        return 2
    print(_describe("Seeded", counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
