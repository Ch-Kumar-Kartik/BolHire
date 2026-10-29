import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import CandidateAuthError, create_auth_router
from app.api.routes import STTTranscriptionError, create_api_router
from app.api.dev_profile import create_dev_profile_router
from app.db.auth_repository import AuthRepository
from app.db.config import DatabaseSettings
from app.db.database import Database
from app.services.candidate_auth import AuthSettings
from app.services.rate_limit import SlidingWindowLimiter
from app.services.candidate_intake import CandidateIntakePipeline
from app.providers.gemini import GeminiLLMProvider
from app.providers.gnani import GnaniSTTProvider, GnaniTTSProvider, SpeechProviderError
from app.providers.llm import LLMProviderError, MockLLMProvider
from app.providers.llm_config import LLMSettings
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
    SessionWorkflowMismatchError,
)
from app.services.wav import InvalidAudioError


def create_app(
    pipeline: TextConversationPipeline | None = None,
    *, stt: STTProvider | None = None, tts: TTSProvider | None = None,
    intake: CandidateIntakePipeline | None = None,
    login_limiter: SlidingWindowLimiter | None = None,
) -> FastAPI:
    database_settings = DatabaseSettings.from_env(os.environ)
    auth_settings = AuthSettings.from_env(os.environ, database_enabled=database_settings.enabled)
    speech_settings = SpeechSettings.from_env(os.environ) if stt is None or tts is None else None
    speech_pricing = SpeechPricing.from_env(os.environ)
    gemini_provider: GeminiLLMProvider | None = None
    if pipeline is None:
        llm_settings = LLMSettings.from_env(os.environ)
        if llm_settings.provider == "gemini":
            gemini_provider = GeminiLLMProvider(llm_settings)
            llm = gemini_provider
        else:
            llm = MockLLMProvider()
        conversation_pipeline = TextConversationPipeline(
            llm=llm, sessions=InMemorySessionStore()
        )
    else:
        conversation_pipeline = pipeline
    intake_pipeline = intake or CandidateIntakePipeline(
        generator=conversation_pipeline.llm, sessions=conversation_pipeline.sessions,
    )
    if intake_pipeline.sessions is not conversation_pipeline.sessions:
        raise ValueError("General chat and candidate intake must share one session store.")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        database = None
        _app.state.database = None
        _app.state.auth_repository = None
        try:
            if database_settings.enabled:
                database = Database(database_settings)
                await database.check_connection()
                _app.state.database = database
                _app.state.auth_repository = AuthRepository(database.sessions)
            yield
        finally:
            try:
                if database is not None:
                    await database.close()
            finally:
                _app.state.database = None
                _app.state.auth_repository = None
                if gemini_provider is not None:
                    await gemini_provider.aclose()

    app = FastAPI(title="BolHire API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("BACKEND_CORS_ORIGIN", "http://localhost:5173")],
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )
    app.state.auth_settings = auth_settings
    app.state.auth_repository = None
    app.state.conversation_pipeline = conversation_pipeline
    app.state.candidate_intake_pipeline = intake_pipeline
    if os.getenv("BOLHIRE_DEV_PROFILE_INSPECTION") == "1":
        app.include_router(create_dev_profile_router(conversation_pipeline.sessions))
    if stt is None or tts is None:
        assert speech_settings is not None
        if speech_settings.provider == "gnani":
            assert speech_settings.api_key is not None and speech_settings.tts_voice is not None
            stt = stt or GnaniSTTProvider(speech_settings.api_key)
            tts = tts or GnaniTTSProvider(speech_settings.api_key, speech_settings.tts_model, speech_settings.tts_voice)
        else:
            stt = stt or MockSTTProvider()
            tts = tts or MockTTSProvider()
    estimated_credits = 0 if isinstance(stt, MockSTTProvider) and isinstance(tts, MockTTSProvider) else None
    speech_provider = "mock" if estimated_credits == 0 else "gnani"

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
        if any(
            item["type"] == "literal_error" and item["loc"][-1] == "workflow"
            for item in error.errors()
        ):
            return error_response(422, "UNSUPPORTED_WORKFLOW", "Supported workflows are general and candidate_intake.")
        if "json_invalid" in error_types:
            return error_response(400, "MALFORMED_JSON", "Request body must be valid JSON.")
        return error_response(422, "INVALID_REQUEST", "Request data is invalid.")

    @app.exception_handler(CandidateAuthError)
    async def candidate_auth_error_handler(_request: Request, error: CandidateAuthError) -> JSONResponse:
        response = error_response(error.status_code, error.code, str(error))
        response.headers["Cache-Control"] = "no-store"
        if error.retry_after is not None:
            response.headers["Retry-After"] = str(error.retry_after)
        if error.clear_cookie:
            response.delete_cookie(**auth_settings.clear_cookie_args())
        return response

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

    @app.exception_handler(SessionWorkflowMismatchError)
    async def workflow_mismatch_handler(
        _request: Request, _error: SessionWorkflowMismatchError
    ) -> JSONResponse:
        return error_response(409, "SESSION_WORKFLOW_MISMATCH", "Start a new conversation to change workflow.")

    @app.exception_handler(LLMGenerationError)
    async def llm_generation_handler(
        _request: Request, _error: LLMGenerationError
    ) -> JSONResponse:
        return error_response(
            502, "LLM_GENERATION_FAILED", "The conversation provider could not reply."
        )

    @app.exception_handler(LLMProviderError)
    async def llm_provider_error_handler(
        _request: Request, error: LLMProviderError
    ) -> JSONResponse:
        return error_response(error.status_code, error.code, str(error))

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

    app.include_router(create_auth_router(
        auth_settings, login_limiter or SlidingWindowLimiter(max_attempts=20, window_seconds=60),
    ))
    app.include_router(create_api_router(
        conversation_pipeline, intake_pipeline, stt, tts, estimated_credits, speech_provider, speech_pricing
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
