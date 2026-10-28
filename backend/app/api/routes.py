import base64
from decimal import Decimal
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, File, Form, UploadFile

from app.models import (
    EstimatedSpeechCostINR, TextChatRequest, TextChatResponse, VoiceAudio,
    VoiceChatResponse, VoiceTimingMS, VoiceUsage, Workflow,
)
from app.providers.gnani import SpeechProviderError
from app.providers.speech import STTProvider, TTSProvider
from app.providers.speech_config import SpeechPricing
from app.services.pipeline import TextConversationPipeline
from app.services.candidate_intake import CandidateIntakePipeline
from app.services.wav import MAX_AUDIO_BYTES, SAMPLE_RATE, validate_wav


class STTTranscriptionError(Exception):
    """The speech recognition provider failed."""


def create_api_router(
    pipeline: TextConversationPipeline, intake: CandidateIntakePipeline,
    stt: STTProvider, tts: TTSProvider,
    estimated_speech_credits: int | None,
    speech_provider: Literal["mock", "gnani"],
    speech_pricing: SpeechPricing,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/chat/text", response_model=TextChatResponse)
    async def text_chat(request: TextChatRequest) -> TextChatResponse:
        selected = intake if request.workflow == "candidate_intake" else pipeline
        return await selected.respond(
            session_id=request.session_id,
            text=request.text,
            language=request.language,
        )

    @router.post("/voice/chat", response_model=VoiceChatResponse)
    async def voice_chat(
        audio: UploadFile = File(...),
        language: Literal["en-IN", "hi-IN"] = Form(...),
        session_id: str | None = Form(None),
        workflow: Workflow = Form("general"),
    ) -> VoiceChatResponse:
        started = perf_counter()
        payload = await audio.read(MAX_AUDIO_BYTES + 1)
        pcm = validate_wav(payload)
        audio_duration = Decimal(len(pcm)) / Decimal(SAMPLE_RATE * 2)
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
        selected = intake if workflow == "candidate_intake" else pipeline
        conversation = await selected.respond(
            session_id=session_id, text=transcript, language=language
        )
        tts_started = perf_counter()
        warning = None
        returned_audio = None
        tts_characters = len(conversation.reply)
        tts_succeeded = False
        try:
            mime_type, synthesized = await tts.synthesize(conversation.reply, language)
            returned_audio = VoiceAudio(
                mime_type=mime_type, base64=base64.b64encode(synthesized).decode("ascii")
            )
            tts_succeeded = True
        except SpeechProviderError as error:
            warning = f"{error} The text reply was saved."
        except Exception:
            warning = "Audio could not be generated. The text reply was saved."
        tts_ms = round((perf_counter() - tts_started) * 1000, 2)
        if speech_provider == "mock":
            cost = EstimatedSpeechCostINR(stt=0, tts=0, total=0)
        else:
            stt_cost = audio_duration * speech_pricing.stt_inr_per_hour / Decimal(3600)
            tts_cost = (
                Decimal(tts_characters) * speech_pricing.tts_inr_per_10000_characters / Decimal(10000)
                if tts_succeeded else None
            )
            cost = EstimatedSpeechCostINR(
                stt=round(float(stt_cost), 8),
                tts=round(float(tts_cost), 8) if tts_cost is not None else None,
                total=round(float(stt_cost + tts_cost), 8) if tts_cost is not None else None,
            )
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
            usage=VoiceUsage(
                audio_duration_seconds=float(audio_duration),
                tts_characters=tts_characters,
                language=language,
                speech_provider=speech_provider,
            ),
            estimated_speech_cost_inr=cost,
            warning=warning,
        )

    return router
