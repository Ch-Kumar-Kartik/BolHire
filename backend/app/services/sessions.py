import asyncio
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.models import ConversationMessage, Workflow
from app.services.candidate_profile import CandidateProfile, CandidateProfileUpdate, apply_candidate_update

MAX_EXCHANGES = 20
SESSION_IDLE_SECONDS = 30 * 60


class SessionNotFoundError(Exception):
    """Raised when a requested session does not exist."""


class SessionExpiredError(Exception):
    """Raised once when an idle session is accessed after expiry."""


class SessionWorkflowMismatchError(Exception):
    """Raised when a session is used for a different conversation workflow."""


@dataclass
class ConversationSession:
    session_id: str
    messages: list[ConversationMessage]
    last_activity: float
    workflow: Workflow = "general"
    candidate_profile: CandidateProfile = field(default_factory=CandidateProfile)
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

    async def resolve(self, session_id: str, *, workflow: Workflow = "general") -> ConversationSession:
        async with self._sessions_lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError
            self._raise_if_expired(session)
            self._ensure_workflow(session, workflow)
            return session

    async def history_for(
        self, session: ConversationSession, *, workflow: Workflow = "general"
    ) -> list[ConversationMessage]:
        async with self._sessions_lock:
            self._ensure_current(session)
            self._raise_if_expired(session)
            self._ensure_workflow(session, workflow)
            return list(session.messages)

    async def candidate_profile_for(self, session: ConversationSession) -> CandidateProfile:
        async with self._sessions_lock:
            self._ensure_current(session)
            self._raise_if_expired(session)
            self._ensure_workflow(session, "candidate_intake")
            return session.candidate_profile

    async def append_exchange(
        self,
        session: ConversationSession,
        exchange: Sequence[ConversationMessage],
    ) -> None:
        async with self._sessions_lock:
            self._ensure_current(session)
            self._raise_if_expired(session)
            self._ensure_workflow(session, "general")
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

    async def create_candidate_exchange(
        self, update: CandidateProfileUpdate, exchange: Sequence[ConversationMessage]
    ) -> ConversationSession:
        """Create the first intake turn only after update and exchange validate."""
        validated_update = self._validated_update(update)
        validated_exchange = self._validated_exchange(exchange)
        profile = apply_candidate_update(CandidateProfile(), validated_update)
        async with self._sessions_lock:
            session_id = self._new_session_id()
            session = ConversationSession(
                session_id=session_id,
                messages=validated_exchange,
                last_activity=self._clock(),
                workflow="candidate_intake",
                candidate_profile=profile,
            )
            self._sessions[session_id] = session
            return session

    async def commit_candidate_exchange(
        self, session: ConversationSession,
        update: CandidateProfileUpdate,
        exchange: Sequence[ConversationMessage],
    ) -> None:
        """Commit one validated intake turn; caller holds session.lock during generation."""
        validated_update = self._validated_update(update)
        validated_exchange = self._validated_exchange(exchange)
        async with self._sessions_lock:
            self._ensure_current(session)
            self._raise_if_expired(session)
            self._ensure_workflow(session, "candidate_intake")
            profile = apply_candidate_update(session.candidate_profile, validated_update)
            messages = (session.messages + validated_exchange)[-(MAX_EXCHANGES * 2):]
            activity = self._clock()
            session.candidate_profile = profile
            session.messages = messages
            session.last_activity = activity

    @staticmethod
    def _validated_update(update: CandidateProfileUpdate) -> CandidateProfileUpdate:
        if not isinstance(update, CandidateProfileUpdate):
            raise TypeError("Candidate update must be a CandidateProfileUpdate.")
        return CandidateProfileUpdate.model_validate(
            update.model_dump(exclude_unset=True, warnings="error")
        )

    @staticmethod
    def _validated_exchange(exchange: Sequence[ConversationMessage]) -> list[ConversationMessage]:
        if len(exchange) != 2 or any(not isinstance(item, ConversationMessage) for item in exchange):
            raise ValueError("An intake turn requires one user message and one assistant message.")
        messages = [ConversationMessage.model_validate(item.model_dump()) for item in exchange]
        if [item.role for item in messages] != ["user", "assistant"] or any(
            not item.content.strip() for item in messages
        ):
            raise ValueError("An intake turn requires a non-empty user and assistant exchange.")
        return messages

    def _new_session_id(self) -> str:
        session_id = secrets.token_urlsafe(24)
        while session_id in self._sessions:
            session_id = secrets.token_urlsafe(24)
        return session_id

    def _ensure_current(self, session: ConversationSession) -> None:
        if self._sessions.get(session.session_id) is not session:
            raise SessionNotFoundError

    @staticmethod
    def _ensure_workflow(session: ConversationSession, workflow: Workflow) -> None:
        if session.workflow != workflow:
            raise SessionWorkflowMismatchError

    def _raise_if_expired(self, session: ConversationSession) -> None:
        if self._clock() - session.last_activity >= self._idle_seconds:
            del self._sessions[session.session_id]
            raise SessionExpiredError
