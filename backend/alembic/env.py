"""Explicit CLI migrations; application startup never invokes Alembic."""
import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.config import DatabaseSettings
from app.db.models import Base


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=Base.metadata,
                      compare_type=True, compare_server_default=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    # A deliberate migration command requires a URL regardless of application enablement.
    settings = DatabaseSettings.from_env({**os.environ, "DATABASE_ENABLED": "1"})
    engine = create_async_engine(settings.url, poolclass=NullPool, hide_parameters=True,
                                 connect_args={"connect_timeout": 5})
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(dialect_name="postgresql", target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    run_migrations(context.config.attributes["connection"])
else:
    asyncio.run(online())
