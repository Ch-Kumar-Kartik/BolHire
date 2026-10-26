import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import STTTranscriptionError, create_api_router
from app.providers.gnani import GnaniSTTProvider, GnaniTTSProvider, SpeechProviderError
from app.providers.llm import MockLLMProvider
from app.providers.speech import MockSTTProvider, MockTTSProvider, STTProvider, TTSProvider
from app.providers.speech_config import SpeechPricing, SpeechSettings
from app.services.pipeline import (
    LLMGenerationError,
    TextConversationPipeline,
)
from app.services.sessions import (
    InMemorySessionStore,
    SessionExpiredError,
    SessionNotFoundError,
)
from app.services.wav import InvalidAudioError


def create_app(
    pipeline: TextConversationPipeline | None = None,
    *, stt: STTProvider | None = None, tts: TTSProvider | None = None,
) -> FastAPI:
    app = FastAPI(title="BolHire API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("BACKEND_CORS_ORIGIN", "http://localhost:5173")],
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )
    conversation_pipeline = pipeline or TextConversationPipeline(
        llm=MockLLMProvider(), sessions=InMemorySessionStore()
    )
    app.state.conversation_pipeline = conversation_pipeline
    if stt is None or tts is None:
        settings = SpeechSettings.from_env(os.environ)
        if settings.provider == "gnani":
            assert settings.api_key is not None and settings.tts_voice is not None
            stt = stt or GnaniSTTProvider(settings.api_key)
            tts = tts or GnaniTTSProvider(settings.api_key, settings.tts_model, settings.tts_voice)
        else:
            stt = stt or MockSTTProvider()
            tts = tts or MockTTSProvider()
    estimated_credits = 0 if isinstance(stt, MockSTTProvider) and isinstance(tts, MockTTSProvider) else None
    speech_provider = "mock" if estimated_credits == 0 else "gnani"
    speech_pricing = SpeechPricing.from_env(os.environ)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        error_types = {item["type"] for item in error.errors()}
        if "empty_message" in error_types:
            return error_response(422, "EMPTY_MESSAGE", "Text cannot be empty.")
        if "unsupported_language" in error_types:
            return error_response(
                422, "UNSUPPORTED_LANGUAGE", "Supported languages are en-IN and hi-IN."
            )
        if any(
            item["type"] == "literal_error" and item["loc"][-1] == "language"
            for item in error.errors()
        ):
            return error_response(422, "UNSUPPORTED_LANGUAGE", "Supported languages are en-IN and hi-IN.")
        if "json_invalid" in error_types:
            return error_response(400, "MALFORMED_JSON", "Request body must be valid JSON.")
        return error_response(422, "INVALID_REQUEST", "Request data is invalid.")

    @app.exception_handler(SessionNotFoundError)
    async def session_not_found_handler(
        _request: Request, _error: SessionNotFoundError
    ) -> JSONResponse:
        return error_response(404, "SESSION_NOT_FOUND", "Session was not found.")

    @app.exception_handler(SessionExpiredError)
    async def session_expired_handler(
        _request: Request, _error: SessionExpiredError
    ) -> JSONResponse:
        return error_response(
            410, "SESSION_EXPIRED", "Session expired. Start a new conversation."
        )

    @app.exception_handler(LLMGenerationError)
    async def llm_generation_handler(
        _request: Request, _error: LLMGenerationError
    ) -> JSONResponse:
        return error_response(
            502, "LLM_GENERATION_FAILED", "The conversation provider could not reply."
        )

    @app.exception_handler(InvalidAudioError)
    async def invalid_audio_handler(_request: Request, error: InvalidAudioError) -> JSONResponse:
        return error_response(422, "INVALID_AUDIO", str(error))

    @app.exception_handler(STTTranscriptionError)
    async def stt_error_handler(_request: Request, _error: STTTranscriptionError) -> JSONResponse:
        return error_response(502, "STT_TRANSCRIPTION_FAILED", "The speech provider could not transcribe audio.")

    @app.exception_handler(SpeechProviderError)
    async def speech_provider_error_handler(_request: Request, error: SpeechProviderError) -> JSONResponse:
        return error_response(error.status_code, error.code, str(error))

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(create_api_router(
        conversation_pipeline, stt, tts, estimated_credits, speech_provider, speech_pricing
    ))
    return app


def error_response(
    status_code: int, code: str, message: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )

app = create_app()
