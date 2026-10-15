import math
import struct
from typing import Protocol

from app.services.wav import SAMPLE_RATE


class STTProvider(Protocol):
    async def transcribe(self, audio: bytes, language: str) -> str: ...


class TTSProvider(Protocol):
    async def synthesize(self, text: str, language: str) -> tuple[str, bytes]: ...


class MockSTTProvider:
    async def transcribe(self, audio: bytes, language: str) -> str:
        return f"Mock transcript [{language}]. No speech recognition was performed."


class MockTTSProvider:
    async def synthesize(self, text: str, language: str) -> tuple[str, bytes]:
        # A brief alert tone; it does not attempt to speak the reply text.
        sample_count = SAMPLE_RATE // 4
        samples = b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)))
            for i in range(sample_count)
        )
        header = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVE"
        header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
        header += b"data" + struct.pack("<I", len(samples))
        return "audio/wav", header + samples
