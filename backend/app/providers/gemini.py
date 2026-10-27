"""Gemini text adapter. One Generate Content request per conversation turn."""

from collections.abc import Sequence

import httpx
from google import genai
from google.genai import errors, types

from app.models import ConversationMessage
from app.providers.llm import LLMProviderError
from app.providers.llm_config import LLMSettings


class GeminiProviderError(LLMProviderError):
    """A safe Gemini failure with a stable application code."""


class GeminiLLMProvider:
    def __init__(self, settings: LLMSettings, *, client: genai.Client | None = None) -> None:
        if settings.provider != "gemini" or settings.api_key is None:
            raise ValueError("Gemini provider requires validated Gemini settings.")
        self._settings = settings
        self._client = client if client is not None else genai.Client(
            api_key=settings.api_key,
            http_options=types.HttpOptions(
                api_version="v1",
                timeout=int(settings.timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    async def generate(self, messages: Sequence[ConversationMessage], language: str) -> str:
        language_instruction = {
            "en-IN": "Reply in English.",
            "hi-IN": "Reply in Hindi. Understand Romanized Hindi and use natural Hindi in the reply.",
        }[language]
        contents = [
            types.Content(
                role="user" if message.role == "user" else "model",
                parts=[types.Part.from_text(text=message.content)],
            )
            for message in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=f"{self._settings.system_prompt}\n{language_instruction}",
            max_output_tokens=self._settings.max_output_tokens,
            thinking_config=(
                types.ThinkingConfig(
                    thinking_level=self._settings.thinking_level,
                    thinking_budget=self._settings.thinking_budget,
                )
                if self._settings.thinking_level is not None or self._settings.thinking_budget is not None
                else None
            ),
            automatic_continuation=False,
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._settings.model, contents=contents, config=config,
            )
        except errors.APIError as error:
            raise _upstream_error(error.code) from None
        except errors.UnknownApiResponseError:
            raise GeminiProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned an invalid reply.") from None
        except (httpx.TimeoutException, TimeoutError):
            raise GeminiProviderError("GEMINI_TIMEOUT", "Gemini did not reply in time.", 504) from None
        except httpx.RequestError:
            raise GeminiProviderError("GEMINI_UNREACHABLE", "Could not reach Gemini.") from None

        try:
            return _extract_reply(response)
        except (AttributeError, TypeError, ValueError, IndexError):
            raise GeminiProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned an invalid reply.") from None

    async def aclose(self) -> None:
        await self._client.aio.aclose()


def _extract_reply(response: types.GenerateContentResponse) -> str:
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        raise GeminiProviderError("GEMINI_BLOCKED_OUTPUT", "Gemini could not provide a reply.")
    if not response.candidates or len(response.candidates) != 1:
        raise GeminiProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned an invalid reply.")
    candidate = response.candidates[0]
    if candidate.finish_reason in {
        types.FinishReason.SAFETY, types.FinishReason.BLOCKLIST,
        types.FinishReason.PROHIBITED_CONTENT, types.FinishReason.SPII,
        types.FinishReason.RECITATION,
    }:
        raise GeminiProviderError("GEMINI_BLOCKED_OUTPUT", "Gemini could not provide a reply.")
    if (candidate.finish_reason != types.FinishReason.STOP or not candidate.content
            or candidate.content.role != "model" or not candidate.content.parts):
        raise GeminiProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned an incomplete reply.")
    parts = candidate.content.parts
    if any(part.text is None for part in parts):
        raise GeminiProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned an invalid reply.")
    reply = "".join(part.text for part in parts if part.text is not None).strip()
    if not reply:
        raise GeminiProviderError("GEMINI_EMPTY_RESPONSE", "Gemini returned no reply.")
    return reply


def _upstream_error(status_code: int) -> GeminiProviderError:
    if status_code in (401, 403):
        return GeminiProviderError("GEMINI_AUTH_FAILED", "Gemini rejected the server credentials or access.")
    if status_code == 404:
        return GeminiProviderError("GEMINI_MODEL_UNAVAILABLE", "The configured Gemini model is unavailable.")
    if status_code == 429:
        return GeminiProviderError("GEMINI_RATE_LIMITED", "Gemini rate limit reached.", 429)
    if status_code in (408, 504):
        return GeminiProviderError("GEMINI_TIMEOUT", "Gemini did not reply in time.", 504)
    if status_code == 400:
        return GeminiProviderError("GEMINI_REJECTED", "Gemini rejected the server request.")
    return GeminiProviderError("GEMINI_UNAVAILABLE", "Gemini could not provide a reply.")
