"""Scheduled cleanup of expired candidate identities and sessions.

Access control never depends on this job: expired identities are rejected by every query.
It only bounds how long expired data is physically retained. Prints counts only.
"""

import argparse
import asyncio
import os

from app.db.auth_repository import AuthRepository, PurgeResult
from app.db.config import DatabaseSettings
from app.db.database import Database


class CleanupError(Exception):
    """Carries only the failing stage and the exception class name: driver messages can contain hosts, users, or SQL."""

    def __init__(self, stage: str, cause: str) -> None:
        super().__init__(f"{stage}: {cause}")
        self.stage = stage
        self.cause = cause


async def _purge(settings: DatabaseSettings, batch_size: int) -> PurgeResult:
    database = Database(settings)
    try:
        try:
            await database.check_connection()
        except Exception as error:  # noqa: BLE001 - reduced to its class name on purpose
            raise CleanupError("connecting", type(error).__name__) from None
        try:
            return await AuthRepository(database.sessions).purge_expired(batch_size=batch_size)
        except Exception as error:  # noqa: BLE001
            raise CleanupError("purging", type(error).__name__) from None
    finally:
        await database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete expired candidate identities and sessions.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Validate configuration without connecting.")
    action.add_argument("--run", action="store_true", help="Delete expired data.")
    parser.add_argument("--confirm-delete", action="store_true", help="Required with --run.")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args(argv)

    if args.run and not args.confirm_delete:
        print("Preflight FAIL: --run requires --confirm-delete.")
        return 1
    if not 1 <= args.batch_size <= 5000:
        print("Preflight FAIL: batch size must be from 1 to 5000.")
        return 1
    try:
        settings = DatabaseSettings.from_env(os.environ)
        if not settings.enabled:
            raise ValueError("DATABASE_ENABLED must be 1 for cleanup.")
    except ValueError as error:
        print(f"Preflight FAIL: {error}")
        return 1

    print("Configuration: PASS (database enabled).")
    if args.check:
        print("No connection made.")
        return 0
    try:
        result = asyncio.run(_purge(settings, args.batch_size))
    except CleanupError as error:
        print(f"Cleanup FAIL during {error.stage}: {error.cause}. The error message is withheld because it can contain credentials or hosts.")
        return 2
    except Exception as error:  # noqa: BLE001
        print(f"Cleanup FAIL: unexpected {type(error).__name__}. The error message is withheld.")
        return 2
    print(f"Removed {result.candidates} expired identities and {result.sessions} expired sessions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
