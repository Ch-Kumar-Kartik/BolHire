import base64
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, File, Form, UploadFile

from app.models import TextChatRequest, TextChatResponse, VoiceAudio, VoiceChatResponse, VoiceTimingMS
from app.providers.gnani import SpeechProviderError
from app.providers.speech import STTProvider, TTSProvider
from app.services.pipeline import TextConversationPipeline
from app.services.wav import MAX_AUDIO_BYTES, validate_wav


class STTTranscriptionError(Exception):
    """The speech recognition provider failed."""


def create_api_router(
    pipeline: TextConversationPipeline, stt: STTProvider, tts: TTSProvider,
    estimated_speech_credits: int | None = 0,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/chat/text", response_model=TextChatResponse)
    async def text_chat(request: TextChatRequest) -> TextChatResponse:
        return await pipeline.respond(
            session_id=request.session_id,
            text=request.text,
            language=request.language,
        )

    @router.post("/voice/chat", response_model=VoiceChatResponse)
    async def voice_chat(
        audio: UploadFile = File(...),
        language: Literal["en-IN", "hi-IN"] = Form(...),
        session_id: str | None = Form(None),
    ) -> VoiceChatResponse:
        started = perf_counter()
        payload = await audio.read(MAX_AUDIO_BYTES + 1)
        validate_wav(payload)
        stt_started = perf_counter()
        try:
            transcript = await stt.transcribe(payload, language)
            if not transcript.strip():
                raise ValueError("Empty transcript")
        except SpeechProviderError:
            raise
        except Exception as error:
            raise STTTranscriptionError from error
        stt_ms = round((perf_counter() - stt_started) * 1000, 2)
        conversation = await pipeline.respond(
            session_id=session_id, text=transcript, language=language
        )
        tts_started = perf_counter()
        warning = None
        returned_audio = None
        try:
            mime_type, synthesized = await tts.synthesize(conversation.reply, language)
            returned_audio = VoiceAudio(
                mime_type=mime_type, base64=base64.b64encode(synthesized).decode("ascii")
            )
        except SpeechProviderError as error:
            warning = f"{error} The text reply was saved."
        except Exception:
            warning = "Audio could not be generated. The text reply was saved."
        tts_ms = round((perf_counter() - tts_started) * 1000, 2)
        return VoiceChatResponse(
            session_id=conversation.session_id,
            transcript=transcript,
            reply=conversation.reply,
            audio=returned_audio,
            timings_ms=VoiceTimingMS(
                stt=stt_ms, llm=conversation.timings_ms.llm,
                tts=tts_ms, total=round((perf_counter() - started) * 1000, 2),
            ),
            estimated_speech_credits=estimated_speech_credits,
            warning=warning,
        )

    return router
