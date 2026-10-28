import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.config import DatabaseSettings


class DatabaseUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The candidate database is unavailable.")


class Database:
    def __init__(self, settings: DatabaseSettings) -> None:
        if not settings.enabled or settings.url is None:
            raise ValueError("Database initialization requires explicit enablement.")
        self.engine = create_async_engine(
            settings.url, echo=False, hide_parameters=True,
            pool_size=5, max_overflow=0, pool_timeout=5, pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def check_connection(self) -> None:
        try:
            async with asyncio.timeout(5):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        except Exception:
            raise DatabaseUnavailableError() from None

    async def close(self) -> None:
        await self.engine.dispose()
