from time import perf_counter

from app.models import ConversationMessage, TextChatResponse, TimingMS
from app.providers.llm import LLMProvider, LLMProviderError
from app.services.sessions import InMemorySessionStore


class LLMGenerationError(Exception):
    """Raised when a provider cannot generate a reply."""


class TextConversationPipeline:
    def __init__(self, *, llm: LLMProvider, sessions: InMemorySessionStore) -> None:
        self._llm = llm
        self._sessions = sessions

    async def respond(
        self, *, session_id: str | None, text: str, language: str
    ) -> TextChatResponse:
        started_at = perf_counter()
        user_message = ConversationMessage(role="user", content=text)

        if session_id is None:
            reply, llm_ms = await self._generate([user_message], language)
            assistant_message = ConversationMessage(role="assistant", content=reply)
            session = await self._sessions.create([user_message, assistant_message])
        else:
            session = await self._sessions.resolve(session_id)
            async with session.lock:
                history = await self._sessions.history_for(session)
                reply, llm_ms = await self._generate(history + [user_message], language)
                assistant_message = ConversationMessage(role="assistant", content=reply)
                await self._sessions.append_exchange(
                    session, [user_message, assistant_message]
                )

        total_ms = round((perf_counter() - started_at) * 1000, 2)
        return TextChatResponse(
            session_id=session.session_id,
            transcript=text,
            reply=reply,
            timings_ms=TimingMS(llm=llm_ms, total=total_ms),
        )

    async def _generate(
        self, messages: list[ConversationMessage], language: str
    ) -> tuple[str, float]:
        started_at = perf_counter()
        try:
            reply = await self._llm.generate(messages, language)
        except LLMProviderError:
            raise
        except Exception as error:
            raise LLMGenerationError from error
        return reply, round((perf_counter() - started_at) * 1000, 2)
