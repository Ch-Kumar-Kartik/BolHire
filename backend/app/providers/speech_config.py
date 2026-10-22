"""Server-side selection and validation for speech providers."""

from collections.abc import Mapping
from dataclasses import dataclass

TIMBRE_V3_VOICES = frozenset({
    "Avinash", "Shivani", "Lakshmi", "Kalpana", "Shubham", "Prakash",
    "Devansh", "Yashraj", "Chetana", "Vasudha", "Kavitha", "Riyaa", "Tarika",
})


@dataclass(frozen=True)
class SpeechSettings:
    provider: str
    api_key: str | None = None
    tts_model: str = "timbre-v3.0"
    tts_voice: str | None = None

    @classmethod
    def from_env(cls, environment: Mapping[str, str]) -> "SpeechSettings":
        provider = environment.get("SPEECH_PROVIDER", "mock").strip().lower()
        if provider == "mock":
            return cls(provider="mock")
        if provider != "gnani":
            raise ValueError("SPEECH_PROVIDER must be mock or gnani.")
        api_key = environment.get("GNANI_API_KEY", "").strip()
        model = environment.get("GNANI_TTS_MODEL", "timbre-v3.0").strip()
        voice = environment.get("GNANI_TTS_VOICE", "").strip()
        if not api_key:
            raise ValueError("GNANI_API_KEY is required when SPEECH_PROVIDER=gnani.")
        if model != "timbre-v3.0":
            raise ValueError("GNANI_TTS_MODEL must be timbre-v3.0 for Task 5.")
        if not voice:
            raise ValueError("GNANI_TTS_VOICE is required when SPEECH_PROVIDER=gnani.")
        if voice not in TIMBRE_V3_VOICES:
            raise ValueError("GNANI_TTS_VOICE must be a documented Timbre v3.0 voice.")
        return cls(provider="gnani", api_key=api_key, tts_model=model, tts_voice=voice)
