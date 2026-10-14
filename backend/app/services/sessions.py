import asyncio
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.models import ConversationMessage

MAX_EXCHANGES = 20
SESSION_IDLE_SECONDS = 30 * 60


class SessionNotFoundError(Exception):
    """Raised when a requested session does not exist."""


class SessionExpiredError(Exception):
    """Raised once when an idle session is accessed after expiry."""


@dataclass
class ConversationSession:
    session_id: str
    messages: list[ConversationMessage]
    last_activity: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class InMemorySessionStore:
    """Single-process session storage with lazy expiration."""

    def __init__(
        self,
        *,
        idle_seconds: float = SESSION_IDLE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._idle_seconds = idle_seconds
        self._sessions: dict[str, ConversationSession] = {}
        self._sessions_lock = asyncio.Lock()

    async def resolve(self, session_id: str) -> ConversationSession:
        async with self._sessions_lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError
            self._raise_if_expired(session)
            return session

    async def history_for(self, session: ConversationSession) -> list[ConversationMessage]:
        async with self._sessions_lock:
            self._ensure_current(session)
            self._raise_if_expired(session)
            return list(session.messages)

    async def append_exchange(
        self,
        session: ConversationSession,
        exchange: Sequence[ConversationMessage],
    ) -> None:
        async with self._sessions_lock:
            self._ensure_current(session)
            self._raise_if_expired(session)
            session.messages.extend(exchange)
            session.messages = session.messages[-(MAX_EXCHANGES * 2) :]
            session.last_activity = self._clock()

    async def create(self, exchange: Sequence[ConversationMessage]) -> ConversationSession:
        async with self._sessions_lock:
            session_id = self._new_session_id()
            session = ConversationSession(
                session_id=session_id,
                messages=list(exchange),
                last_activity=self._clock(),
            )
            self._sessions[session_id] = session
            return session

    def _new_session_id(self) -> str:
        session_id = secrets.token_urlsafe(24)
        while session_id in self._sessions:
            session_id = secrets.token_urlsafe(24)
        return session_id

    def _ensure_current(self, session: ConversationSession) -> None:
        if self._sessions.get(session.session_id) is not session:
            raise SessionNotFoundError

    def _raise_if_expired(self, session: ConversationSession) -> None:
        if self._clock() - session.last_activity >= self._idle_seconds:
            del self._sessions[session.session_id]
            raise SessionExpiredError
