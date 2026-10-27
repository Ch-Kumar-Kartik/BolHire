"""Server-side selection and validation for the conversation provider."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass


DEFAULT_SYSTEM_PROMPT = (
    "You are BolHire, an early-stage voice-based career assistant for people in India. "
    "Understand English, Hindi, and Romanized Hindi. Reply in the selected conversation "
    "language using concise, natural sentences that are easy to speak. Avoid Markdown. "
    "Do not invent job openings, candidate matches, or recruiter actions. Do not claim "
    "candidate information was saved to a database."
)


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str | None = None
    model: str = "gemini-3.5-flash-lite"
    timeout_seconds: float = 20.0
    max_output_tokens: int = 1024
    thinking_budget: int | None = None
    thinking_level: str | None = None
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @classmethod
    def from_env(cls, environment: Mapping[str, str]) -> "LLMSettings":
        provider = environment.get("LLM_PROVIDER", "mock").strip().lower()
        if provider == "mock":
            return cls(provider="mock")
        if provider != "gemini":
            raise ValueError("LLM_PROVIDER must be mock or gemini.")

        api_key = environment.get("GEMINI_API_KEY", "").strip()
        model = environment.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", model):
            raise ValueError("GEMINI_MODEL must be a valid model identifier.")

        try:
            timeout = float(environment.get("GEMINI_TIMEOUT_SECONDS", "20"))
        except ValueError as error:
            raise ValueError("GEMINI_TIMEOUT_SECONDS must be between 1 and 120.") from error
        if not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise ValueError("GEMINI_TIMEOUT_SECONDS must be between 1 and 120.")

        try:
            max_output_tokens = int(environment.get("GEMINI_MAX_OUTPUT_TOKENS", "1024"))
        except ValueError as error:
            raise ValueError("GEMINI_MAX_OUTPUT_TOKENS must be between 1 and 65536.") from error
        if not 1 <= max_output_tokens <= 65536:
            raise ValueError("GEMINI_MAX_OUTPUT_TOKENS must be between 1 and 65536.")

        model_name = model.rsplit("/", 1)[-1]
        is_gemini_3 = model_name.startswith("gemini-3.")
        raw_budget = environment.get("GEMINI_THINKING_BUDGET")
        raw_level = environment.get("GEMINI_THINKING_LEVEL")
        if is_gemini_3 and raw_budget is not None:
            raise ValueError("GEMINI_THINKING_BUDGET is not supported for Gemini 3.x; use GEMINI_THINKING_LEVEL.")
        if not is_gemini_3 and raw_level is not None:
            raise ValueError("GEMINI_THINKING_LEVEL is only supported for Gemini 3.x.")
        if raw_budget is None:
            thinking_budget = 0 if model_name == "gemini-2.5-flash" else None
        else:
            try:
                thinking_budget = int(raw_budget)
            except ValueError as error:
                raise ValueError("GEMINI_THINKING_BUDGET must be -1 or between 0 and 24576.") from error
            if not -1 <= thinking_budget <= 24576:
                raise ValueError("GEMINI_THINKING_BUDGET must be -1 or between 0 and 24576.")
        thinking_level = None
        if is_gemini_3:
            thinking_level = (
                raw_level.strip().lower() if raw_level is not None
                else "minimal" if model_name == "gemini-3.5-flash-lite" else None
            )
            if thinking_level is not None and thinking_level not in {"minimal", "low", "medium", "high"}:
                raise ValueError("GEMINI_THINKING_LEVEL must be minimal, low, medium, or high.")

        system_prompt = environment.get("GEMINI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT).strip()
        if not system_prompt or len(system_prompt) > 4000:
            raise ValueError("GEMINI_SYSTEM_PROMPT must contain 1 to 4000 characters.")

        return cls(
            provider="gemini", api_key=api_key, model=model,
            timeout_seconds=timeout, max_output_tokens=max_output_tokens,
            thinking_budget=thinking_budget, thinking_level=thinking_level,
            system_prompt=system_prompt,
        )
