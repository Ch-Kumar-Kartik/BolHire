from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import create_api_router
from app.providers.llm import MockLLMProvider
from app.services.pipeline import (
    LLMGenerationError,
    TextConversationPipeline,
)
from app.services.sessions import (
    InMemorySessionStore,
    SessionExpiredError,
    SessionNotFoundError,
)


def create_app(pipeline: TextConversationPipeline | None = None) -> FastAPI:
    app = FastAPI(title="BolHire API")
    conversation_pipeline = pipeline or TextConversationPipeline(
        llm=MockLLMProvider(), sessions=InMemorySessionStore()
    )
    app.state.conversation_pipeline = conversation_pipeline

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

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(create_api_router(conversation_pipeline))
    return app


def error_response(
    status_code: int, code: str, message: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )

app = create_app()
