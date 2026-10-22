"""Gnani REST adapters. Neither client retries a billable request."""

import struct
from collections.abc import Callable

import httpx

STT_URL = "https://api.vachana.ai/stt/v3"
TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
MAX_TTS_BYTES = 10 * 1024 * 1024


class SpeechProviderError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.reason = reason


def _upstream_error(response: httpx.Response, stage: str) -> SpeechProviderError:
    if response.status_code in (401, 403):
        return SpeechProviderError(f"GNANI_{stage}_AUTH_FAILED", "Gnani rejected the API key or account access. Check server credentials.")
    if response.status_code == 429:
        return SpeechProviderError(f"GNANI_{stage}_RATE_LIMITED", "Gnani rate limit reached. Try again later.", 429)
    if response.status_code == 400:
        return SpeechProviderError(f"GNANI_{stage}_REJECTED", f"Gnani rejected the {stage} request. Check the configured voice, model, language, and audio format.")
    return SpeechProviderError(f"GNANI_{stage}_UNAVAILABLE", f"Gnani {stage} service is unavailable. Try again later.")


class GnaniSTTProvider:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self._transport = transport

    async def transcribe(self, audio: bytes, language: str) -> str:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=20.0) as client:
                response = await client.post(
                    STT_URL,
                    headers={"X-API-Key-ID": self._api_key},
                    data={"language_code": language},
                    files={"audio_file": ("recording.wav", audio, "audio/wav")},
                )
        except httpx.TimeoutException as error:
            raise SpeechProviderError("GNANI_STT_TIMEOUT", "Gnani speech recognition timed out. Submit the recording again if you wish.", 504) from error
        except httpx.RequestError as error:
            raise SpeechProviderError("GNANI_STT_UNREACHABLE", "Could not reach Gnani speech recognition.") from error
        if response.status_code != 200:
            raise _upstream_error(response, "STT")
        try:
            body = response.json()
        except ValueError as error:
            raise SpeechProviderError("GNANI_STT_INVALID_RESPONSE", "Gnani returned an unreadable transcription response.") from error
        if not isinstance(body, dict) or body.get("success") is not True:
            raise SpeechProviderError("GNANI_STT_INVALID_RESPONSE", "Gnani returned an invalid transcription response.")
        transcript = body.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            raise SpeechProviderError("GNANI_STT_EMPTY_TRANSCRIPT", "Gnani returned no speech transcript. Try a clearer recording.")
        return transcript.strip()


class GnaniTTSProvider:
    def __init__(
        self, api_key: str, model: str, voice: str,
        *, transport: httpx.AsyncBaseTransport | None = None,
        diagnostics: Callable[[dict[str, int | str | bool | None]], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._transport = transport
        self._diagnostics = diagnostics

    async def synthesize(self, text: str, language: str) -> tuple[str, bytes]:
        metadata: dict[str, int | str | bool | None] = {
            "http_status": None, "content_type": None, "content_length": None,
            "content_encoding": None,
            "received_bytes": 0, "riff_signature": False, "wave_signature": False,
            "riff_size": None, "data_size": None, "failure_reason": None,
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
                async with client.stream(
                    "POST", TTS_URL,
                    headers={"X-API-Key-ID": self._api_key},
                    json={
                        "text": text, "voice": self._voice, "model": self._model,
                        "language": language,
                        "audio_config": {
                            "sample_rate": 48000, "num_channels": 1,
                            "sample_width": 2, "encoding": "linear_pcm", "container": "wav",
                        },
                    },
                ) as response:
                    metadata["http_status"] = response.status_code
                    metadata["content_type"] = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()[:40]
                    raw_length = response.headers.get("content-length", "")
                    metadata["content_length"] = int(raw_length) if raw_length.isdecimal() else None
                    metadata["content_encoding"] = response.headers.get("content-encoding", "").strip().lower()[:40] or None
                    if response.status_code != 200:
                        raise _upstream_error(response, "TTS")
                    mime_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if mime_type != "audio/wav":
                        raise SpeechProviderError("GNANI_TTS_INVALID_RESPONSE", "Gnani returned an unexpected audio format.", reason="content_type")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        metadata["received_bytes"] = total
                        if total > MAX_TTS_BYTES:
                            raise SpeechProviderError("GNANI_TTS_INVALID_RESPONSE", "Gnani returned audio exceeding the response limit.", reason="response_limit")
                        chunks.append(chunk)
            audio = b"".join(chunks)
            metadata.update(_wav_diagnostics(audio))
            if metadata["content_encoding"] in (None, "identity") and metadata["content_length"] is not None and metadata["content_length"] != len(audio):
                raise SpeechProviderError("GNANI_TTS_INVALID_RESPONSE", "Gnani returned incomplete audio.", reason="content_length_mismatch")
            return mime_type, normalize_tts_wav(audio)
        except httpx.TimeoutException as error:
            metadata["failure_reason"] = "timeout"
            raise SpeechProviderError("GNANI_TTS_TIMEOUT", "Gnani speech synthesis timed out.", 504) from error
        except httpx.RequestError as error:
            metadata["failure_reason"] = "transport_error"
            raise SpeechProviderError("GNANI_TTS_UNREACHABLE", "Could not reach Gnani speech synthesis.") from error
        except SpeechProviderError as error:
            metadata["failure_reason"] = error.reason or error.code.lower()
            raise
        finally:
            if self._diagnostics is not None:
                self._diagnostics(metadata)


def _wav_diagnostics(audio: bytes) -> dict[str, int | bool | None]:
    result: dict[str, int | bool | None] = {
        "riff_signature": audio[:4] == b"RIFF", "wave_signature": audio[8:12] == b"WAVE",
        "riff_size": struct.unpack_from("<I", audio, 4)[0] if len(audio) >= 8 else None,
        "data_size": None,
    }
    offset = 12
    while offset + 8 <= len(audio):
        chunk_size = struct.unpack_from("<I", audio, offset + 4)[0]
        if audio[offset:offset + 4] == b"data":
            result["data_size"] = chunk_size
            break
        end = offset + 8 + chunk_size + (chunk_size & 1)
        if end > len(audio):
            break
        offset = end
    return result


def _invalid_wav(reason: str, message: str = "Gnani returned malformed WAV audio.") -> SpeechProviderError:
    return SpeechProviderError("GNANI_TTS_INVALID_RESPONSE", message, reason=reason)


def validate_tts_wav(audio: bytes) -> float:
    """Validate complete 48 kHz mono PCM WAV and return its duration in seconds.

    Streaming encoders may leave RIFF/data sizes as 0xffffffff. All concrete
    chunk lengths and the actual PCM payload are still checked.
    """
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise _invalid_wav("signature")
    riff_size = struct.unpack_from("<I", audio, 4)[0]
    if riff_size != 0xffffffff and riff_size != len(audio) - 8:
        raise _invalid_wav("riff_size_mismatch", "Gnani returned incomplete WAV audio.")
    offset = 12
    format_seen = False
    data_bytes: int | None = None
    while offset < len(audio):
        if len(audio) - offset < 8:
            raise _invalid_wav("incomplete_chunk_header")
        chunk_id = audio[offset:offset + 4]
        declared = struct.unpack_from("<I", audio, offset + 4)[0]
        start = offset + 8
        if declared == 0xffffffff:
            if chunk_id != b"data":
                raise _invalid_wav("placeholder_non_data_chunk")
            end = len(audio)
        else:
            end = start + declared
            if end > len(audio) or end + (declared & 1) > len(audio):
                raise _invalid_wav("truncated_chunk", "Gnani returned incomplete WAV audio.")
        if chunk_id == b"fmt ":
            if format_seen or declared < 16:
                raise _invalid_wav("invalid_fmt_chunk")
            encoding, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", audio, start)
            if encoding == 0xfffe:
                pcm_guid = bytes.fromhex("0100000000001000800000aa00389b71")
                if declared < 40 or struct.unpack_from("<H", audio, start + 16)[0] < 22 or audio[start + 24:start + 40] != pcm_guid:
                    raise _invalid_wav("unsupported_extensible_format")
                valid_bits = struct.unpack_from("<H", audio, start + 18)[0]
                if valid_bits != 16:
                    raise _invalid_wav("unsupported_valid_bits")
            elif encoding != 1:
                raise _invalid_wav("unsupported_encoding")
            if (channels, rate, byte_rate, align, bits) != (1, 48000, 96000, 2, 16):
                raise _invalid_wav("unsupported_pcm_format")
            format_seen = True
        elif chunk_id == b"data":
            if data_bytes is not None:
                raise _invalid_wav("multiple_data_chunks")
            data_bytes = end - start
            if declared == 0xffffffff and end != len(audio):
                raise _invalid_wav("placeholder_data_not_terminal")
        offset = end + (declared & 1) if declared != 0xffffffff else end
    if not format_seen or data_bytes is None or data_bytes == 0 or data_bytes % 2:
        raise _invalid_wav("missing_or_invalid_pcm_data")
    return data_bytes / 96000


def normalize_tts_wav(audio: bytes) -> bytes:
    """Replace streaming size placeholders with concrete lengths, leaving PCM intact."""
    validate_tts_wav(audio)
    riff_placeholder = struct.unpack_from("<I", audio, 4)[0] == 0xffffffff
    data_size_offset: int | None = None
    offset = 12
    while offset < len(audio):
        chunk_id = audio[offset:offset + 4]
        declared = struct.unpack_from("<I", audio, offset + 4)[0]
        if chunk_id == b"data" and declared == 0xffffffff:
            data_size_offset = offset + 4
            break
        offset += 8 + declared + (declared & 1)
    if not riff_placeholder and data_size_offset is None:
        return audio
    normalized = bytearray(audio)
    if riff_placeholder:
        struct.pack_into("<I", normalized, 4, len(audio) - 8)
    if data_size_offset is not None:
        struct.pack_into("<I", normalized, data_size_offset, len(audio) - data_size_offset - 4)
    result = bytes(normalized)
    validate_tts_wav(result)
    return result
