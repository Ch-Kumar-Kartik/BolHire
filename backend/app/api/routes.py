from fastapi import APIRouter

from app.models import TextChatRequest, TextChatResponse
from app.services.pipeline import TextConversationPipeline


def create_api_router(pipeline: TextConversationPipeline) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/chat/text", response_model=TextChatResponse)
    async def text_chat(request: TextChatRequest) -> TextChatResponse:
        return await pipeline.respond(
            session_id=request.session_id,
            text=request.text,
            language=request.language,
        )

    return router
