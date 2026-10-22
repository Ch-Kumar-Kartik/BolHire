"""Manual, single-request Gnani STT smoke test.

Load credentials with `uv run --env-file ...` before invoking this module.
`--check` never sends a request; `--run --confirm-billable` sends one.
"""

import argparse
import asyncio
import os
from pathlib import Path
from time import perf_counter

from app.providers.gnani import GnaniSTTProvider, SpeechProviderError
from app.providers.speech_config import SpeechSettings
from app.services.wav import MAX_AUDIO_BYTES, InvalidAudioError, validate_wav


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight or run one Gnani STT request.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Validate configuration and optional WAV without a request.")
    action.add_argument("--run", action="store_true", help="Send exactly one STT request.")
    parser.add_argument("--confirm-billable", action="store_true", help="Required with --run after explicit approval.")
    parser.add_argument("--audio", type=Path, help="Previously verified 16 kHz mono 16-bit PCM WAV.")
    parser.add_argument("--language", choices=("en-IN", "hi-IN"), default="en-IN")
    args = parser.parse_args(argv)

    if args.run and (not args.confirm_billable or args.audio is None):
        print("Preflight FAIL: --run requires --confirm-billable and --audio.")
        return 1

    try:
        settings = SpeechSettings.from_env(os.environ)
        if settings.provider != "gnani" or settings.api_key is None:
            raise ValueError("SPEECH_PROVIDER must be gnani for this smoke test.")
        if args.audio is not None:
            with args.audio.open("rb") as audio_file:
                payload = audio_file.read(MAX_AUDIO_BYTES + 1)
            pcm = validate_wav(payload)
        else:
            payload = None
            pcm = None
    except (ValueError, InvalidAudioError) as error:
        print(f"Preflight FAIL: {error}")
        return 1
    except OSError:
        print("Preflight FAIL: WAV file could not be read.")
        return 1

    print("Configuration: PASS (Gnani selected; key, model, and catalog voice valid).")
    if pcm is not None:
        print(f"Audio: PASS (16 kHz mono 16-bit PCM WAV; {len(pcm) / 32000:.2f} s).")
    if args.check:
        print("No API request made.")
        return 0

    assert payload is not None
    started = perf_counter()
    try:
        transcript = asyncio.run(GnaniSTTProvider(settings.api_key).transcribe(payload, args.language))
    except SpeechProviderError as error:
        print(f"STT error: {error.code}: {error}")
        print(f"Latency: {(perf_counter() - started) * 1000:.2f} ms")
        return 2
    except Exception:
        print("STT error: UNEXPECTED: local smoke test failed.")
        print(f"Latency: {(perf_counter() - started) * 1000:.2f} ms")
        return 2
    print(f"Transcript: {transcript}")
    print(f"Latency: {(perf_counter() - started) * 1000:.2f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
