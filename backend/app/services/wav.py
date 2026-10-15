import struct

MAX_AUDIO_BYTES = 2 * 1024 * 1024
MAX_AUDIO_SECONDS = 30
SAMPLE_RATE = 16_000


class InvalidAudioError(Exception):
    """Uploaded audio is not supported by the voice pipeline."""


def validate_wav(payload: bytes) -> bytes:
    if len(payload) > MAX_AUDIO_BYTES:
        raise InvalidAudioError("Audio exceeds the 2 MB limit.")
    if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
        raise InvalidAudioError("Audio must be a valid RIFF/WAVE file.")
    if struct.unpack_from("<I", payload, 4)[0] != len(payload) - 8:
        raise InvalidAudioError("WAV size does not match its RIFF header.")

    offset = 12
    format_found = False
    audio_data: bytes | None = None
    while offset < len(payload):
        if len(payload) - offset < 8:
            raise InvalidAudioError("WAV contains an incomplete chunk header.")
        chunk_type = payload[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", payload, offset + 4)[0]
        start = offset + 8
        end = start + chunk_size
        if end > len(payload) or end + (chunk_size % 2) > len(payload):
            raise InvalidAudioError("WAV chunk size exceeds the uploaded data.")
        if chunk_type == b"fmt ":
            if format_found or chunk_size < 16:
                raise InvalidAudioError("WAV format chunk is invalid.")
            encoding, channels, rate, byte_rate, block_align, bits = struct.unpack_from(
                "<HHIIHH", payload, start
            )
            if (encoding, channels, rate, byte_rate, block_align, bits) != (
                1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16
            ):
                raise InvalidAudioError("Audio must be mono 16 kHz, 16-bit PCM WAV.")
            format_found = True
        elif chunk_type == b"data":
            if audio_data is not None:
                raise InvalidAudioError("WAV contains multiple data chunks.")
            audio_data = payload[start:end]
        offset = end + (chunk_size % 2)

    if not format_found or not audio_data or len(audio_data) % 2:
        raise InvalidAudioError("WAV has no valid PCM audio data.")
    if len(audio_data) // 2 > SAMPLE_RATE * MAX_AUDIO_SECONDS:
        raise InvalidAudioError("Audio exceeds the 30-second limit.")
    return audio_data
