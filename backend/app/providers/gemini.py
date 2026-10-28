"""Gemini text adapter. One Generate Content request per conversation turn."""

from collections.abc import Sequence
import json
import logging

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app.models import ConversationMessage
from app.providers.llm import LLMProviderError
from app.providers.llm_config import LLMSettings
from app.services.candidate_intake import CandidateIntakeProposal
from app.services.candidate_profile import CandidateProfile

logger = logging.getLogger(__name__)


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
        contents = _contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=f"{self._settings.system_prompt}\n{language_instruction}",
            max_output_tokens=self._settings.max_output_tokens,
            thinking_config=self._thinking_config(),
            automatic_continuation=False,
        )
        response = await self._request(contents, config)

        try:
            return _extract_reply(response)
        except (AttributeError, TypeError, ValueError, IndexError):
            raise GeminiProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned an invalid reply.") from None

    async def generate_intake(
        self, messages: Sequence[ConversationMessage], profile: CandidateProfile, language: str,
    ) -> CandidateIntakeProposal:
        """Return one schema-constrained proposal for the latest candidate turn."""
        if not messages or messages[-1].role != "user":
            raise ValueError("Candidate intake requires a latest user message.")
        language_instruction = {
            "en-IN": "Write the acknowledgement in English.",
            "hi-IN": "Write the acknowledgement in natural Hindi. Understand Romanized Hindi input.",
        }[language]
        profile_json = json.dumps(profile.model_dump(mode="json"), ensure_ascii=False)
        instruction = (
            f"{self._settings.system_prompt}\n{language_instruction}\n"
            "For candidate intake, return only the requested JSON object. Extract updates only from "
            "the latest user utterance; use earlier messages and the current profile only to "
            "understand explicit corrections. Never infer years, role, location, or skills. "
            "Copy names, roles, skills, levels, and locations as spoken. Omit unchanged "
            "optional fields; never send null. Send empty arrays for unused operation lists; "
            "use skills_add and skills_remove for incremental changes, skills_replace only for "
            "explicit replacement, clear_fields for explicit clearing, and decline_fields only "
            "when the candidate explicitly declines. For every changed profile field, include "
            "one evidence quote copied exactly from the latest utterance and label it with "
            "the profile field name; use experience for years or level and technical_skills "
            "for skill operations. The acknowledgement must be one of: "
            "English: 'Thanks for sharing.', 'Got it.', 'Thank you.', 'Understood.'; "
            "Hindi: 'धन्यवाद।', 'ठीक है।', 'समझ गया।', 'समझ गई।'. "
            "The server asks follow-up "
            f"questions. Current validated profile: {profile_json}"
        )
        config = types.GenerateContentConfig(
            system_instruction=instruction,
            max_output_tokens=self._settings.max_output_tokens,
            thinking_config=self._thinking_config(),
            automatic_continuation=False,
            response_mime_type="application/json",
            response_schema=_intake_response_schema(),
        )
        try:
            response = await self._request(_contents(messages), config, diagnose_intake_rejection=True)
            return CandidateIntakeProposal.model_validate_json(_extract_reply(response))
        except (ValidationError, ValueError, TypeError, AttributeError, IndexError):
            raise GeminiProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned an invalid intake reply.") from None

    def _thinking_config(self) -> types.ThinkingConfig | None:
        if self._settings.thinking_level is None and self._settings.thinking_budget is None:
            return None
        return types.ThinkingConfig(
            thinking_level=self._settings.thinking_level,
            thinking_budget=self._settings.thinking_budget,
        )

    async def _request(
        self, contents: list[types.Content], config: types.GenerateContentConfig,
        *, diagnose_intake_rejection: bool = False,
    ) -> types.GenerateContentResponse:
        try:
            return await self._client.aio.models.generate_content(
                model=self._settings.model, contents=contents, config=config,
            )
        except errors.APIError as error:
            if diagnose_intake_rejection and error.code == 400:
                _log_intake_rejection(error)
            raise _upstream_error(error.code) from None
        except errors.UnknownApiResponseError:
            raise GeminiProviderError("GEMINI_INVALID_RESPONSE", "Gemini returned an invalid reply.") from None
        except (httpx.TimeoutException, TimeoutError):
            raise GeminiProviderError("GEMINI_TIMEOUT", "Gemini did not reply in time.", 504) from None
        except httpx.RequestError:
            raise GeminiProviderError("GEMINI_UNREACHABLE", "Could not reach Gemini.") from None

    async def aclose(self) -> None:
        await self._client.aio.aclose()


def _log_intake_rejection(error: errors.APIError) -> None:
    """Log only fixed diagnostic labels, never the upstream message or request."""
    api_status = error.status if isinstance(error.status, str) and error.status in {
        "INVALID_ARGUMENT", "FAILED_PRECONDITION", "OUT_OF_RANGE", "BAD_REQUEST",
    } else "unavailable"
    message = error.message.casefold() if isinstance(error.message, str) else ""
    if any(term in message for term in (
        "response_schema", "responseschema", "response_json_schema", "responsejsonschema",
        "responseformat", "response_format", "schema", "too many states", "branching",
    )):
        category = "response_schema"
    elif any(term in message for term in (
        "thinking_config", "thinkingconfig", "thinking_level", "thinkinglevel",
        "thinking_budget", "thinkingbudget",
    )):
        category = "thinking_config"
    elif any(term in message for term in (
        "maxoutputtokens", "max_output_tokens", "generationconfig", "generation_config",
        "candidatecount", "candidate_count", "temperature", "top_p", "topp", "top_k", "topk",
        "model parameter", "model_parameter",
    )):
        category = "model_parameter"
    else:
        category = "unknown"
    logger.warning(
        "Gemini structured intake rejected: http_status=400 api_status=%s category=%s",
        api_status, category,
    )


def _intake_response_schema() -> dict[str, object]:
    """Keep generation constraints small; CandidateIntakeProposal validates locally."""
    string = {"type": "STRING"}
    string_array = {"type": "ARRAY", "items": string}
    profile_fields = [
        "name", "target_role", "experience", "technical_skills",
        "preferred_location", "work_mode",
    ]
    return {
        "type": "OBJECT",
        "properties": {
            "update": {
                "type": "OBJECT",
                "properties": {
                    "name": string,
                    "target_role": string,
                    "experience_years": {"type": "NUMBER"},
                    "experience_level": string,
                    "preferred_location": string,
                    "work_mode": {"type": "STRING", "enum": [
                        "remote", "hybrid", "onsite", "no_preference",
                    ]},
                    "skills_replace": string_array,
                    "skills_add": string_array,
                    "skills_remove": string_array,
                    "clear_fields": {"type": "ARRAY", "items": {
                        "type": "STRING", "enum": [
                            "name", "target_role", "experience", "experience_years",
                            "experience_level", "technical_skills", "preferred_location", "work_mode",
                        ],
                    }},
                    "decline_fields": {"type": "ARRAY", "items": {
                        "type": "STRING", "enum": profile_fields,
                    }},
                },
                "required": ["skills_add", "skills_remove", "clear_fields", "decline_fields"],
            },
            "acknowledgement": string,
            "evidence": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "field": {"type": "STRING", "enum": profile_fields},
                        "quote": string,
                    },
                    "required": ["field", "quote"],
                },
            },
        },
        "required": ["update", "acknowledgement", "evidence"],
    }


def _contents(messages: Sequence[ConversationMessage]) -> list[types.Content]:
    return [
        types.Content(
            role="user" if message.role == "user" else "model",
            parts=[types.Part.from_text(text=message.content)],
        )
        for message in messages
    ]


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
