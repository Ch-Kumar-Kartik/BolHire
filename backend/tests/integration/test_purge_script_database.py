from datetime import datetime, timedelta, timezone

import asyncio
import pytest
from sqlalchemy import func, select, update

from app.db.candidate_repository import CandidateRepository
from app.db.config import DatabaseSettings
from app.db.database import Database
from app.db.models import Candidate, CandidateProfileRow
from app.services.candidate_auth import RecoveryKey
from app.services.candidate_profile import CandidateProfile
from scripts import purge_expired_candidates as script

pytestmark = pytest.mark.integration


def test_script_purges_a_real_database_and_prints_counts_only(db_url, monkeypatch, capsys):
    async def seed():
        database = Database(DatabaseSettings(True, db_url))
        try:
            repo = CandidateRepository(database.sessions)
            meta = dict(consent_version="v", consented_at=datetime.now(timezone.utc),
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1))
            for name in ("Gone", "Kept"):
                result = await repo.create(CandidateProfile(name=name), recovery_secret_hash=RecoveryKey.generate().verifier(), **meta)
                if name == "Gone":
                    async with database.sessions.begin() as session:
                        past = func.statement_timestamp() - timedelta(days=2)
                        await session.execute(update(CandidateProfileRow).where(
                            CandidateProfileRow.candidate_id == result.candidate_id).values(
                            created_at=past, updated_at=past, expires_at=func.statement_timestamp() - timedelta(days=1)))
        finally:
            await database.close()

    async def remaining():
        database = Database(DatabaseSettings(True, db_url))
        try:
            async with database.sessions() as session:
                return await session.scalar(select(func.count()).select_from(Candidate))
        finally:
            await database.close()

    asyncio.run(seed())
    monkeypatch.setenv("DATABASE_ENABLED", "1")
    monkeypatch.setenv("DATABASE_URL", db_url.render_as_string(hide_password=False))
    assert script.main(["--run", "--confirm-delete"]) == 0
    printed = capsys.readouterr().out
    assert "Removed 1 expired identities and 0 expired sessions." in printed and "Gone" not in printed
    assert asyncio.run(remaining()) == 1
