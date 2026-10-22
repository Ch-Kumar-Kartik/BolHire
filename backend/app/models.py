from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError

SUPPORTED_LANGUAGES = frozenset({"en-IN", "hi-IN"})


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class TextChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    text: str
    language: str

    @field_validator("text")
    @classmethod
    def message_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise PydanticCustomError(
                "empty_message", "Text must contain non-whitespace characters."
            )
        return value

    @field_validator("language")
    @classmethod
    def language_must_be_supported(cls, value: str) -> str:
        if value not in SUPPORTED_LANGUAGES:
            raise PydanticCustomError(
                "unsupported_language",
                "Language must be one of: en-IN, hi-IN.",
            )
        return value


class TimingMS(BaseModel):
    llm: float
    total: float


class TextChatResponse(BaseModel):
    session_id: str
    transcript: str
    reply: str
    audio: None = None
    timings_ms: TimingMS
    estimated_speech_credits: Literal[0] = 0


class VoiceAudio(BaseModel):
    mime_type: Literal["audio/wav"]
    base64: str


class VoiceTimingMS(BaseModel):
    stt: float
    llm: float
    tts: float
    total: float


class VoiceChatResponse(BaseModel):
    session_id: str
    transcript: str
    reply: str
    audio: VoiceAudio | None
    timings_ms: VoiceTimingMS
    estimated_speech_credits: int | None = 0
    warning: str | None = None
