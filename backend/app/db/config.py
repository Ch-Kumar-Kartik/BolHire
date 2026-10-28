from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy.engine import URL, make_url


@dataclass(frozen=True)
class DatabaseSettings:
    enabled: bool = False
    url: str | URL | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "DatabaseSettings":
        enabled = env.get("DATABASE_ENABLED", "0")
        if enabled not in {"0", "1"}:
            raise ValueError("DATABASE_ENABLED must be 0 or 1.")
        if enabled == "0":
            return cls()
        raw = env.get("DATABASE_URL", "").strip()
        try:
            url = make_url(raw)
            valid = url.drivername == "postgresql+psycopg" and bool(url.host and url.database)
        except Exception:
            valid = False
        if not valid:
            raise ValueError("DATABASE_URL must specify PostgreSQL with the psycopg driver and a host/database.") from None
        return cls(enabled=True, url=raw)
