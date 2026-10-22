"""Manual, single-request Gnani TTS smoke test.

From backend, load the root .env with `uv run --env-file ../.env`.
`--check` never sends a request; `--run --confirm-billable` sends one.
"""

import argparse
import asyncio
import os
from pathlib import Path
from time import perf_counter

from app.providers.gnani import GnaniTTSProvider, SpeechProviderError, validate_tts_wav
from app.providers.speech_config import SpeechSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight or run one Gnani TTS request.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Validate configuration without a request.")
    action.add_argument("--run", action="store_true", help="Send exactly one TTS request.")
    parser.add_argument("--confirm-billable", action="store_true", help="Required with --run after explicit approval.")
    parser.add_argument("--text", default="Hello, welcome to BolHire.")
    parser.add_argument("--language", choices=("en-IN", "hi-IN"), default="en-IN")
    parser.add_argument("--output", type=Path, default=Path("smoke-output.wav"))
    parser.add_argument("--diagnostic", action="store_true", help="Print safe response metadata; requires --run and --confirm-billable.")
    args = parser.parse_args(argv)

    if args.run and not args.confirm_billable:
        print("Preflight FAIL: --run requires --confirm-billable.")
        return 1
    if args.diagnostic and not args.run:
        print("Preflight FAIL: --diagnostic requires --run and --confirm-billable.")
        return 1
    if not args.text.strip():
        print("Preflight FAIL: TTS text must not be empty.")
        return 1
    try:
        settings = SpeechSettings.from_env(os.environ)
        if settings.provider != "gnani" or not settings.api_key or not settings.tts_voice:
            raise ValueError("SPEECH_PROVIDER must be gnani for this smoke test.")
    except ValueError as error:
        print(f"Preflight FAIL: {error}")
        return 1
    if args.output.exists():
        print("Preflight FAIL: output file already exists; choose a new --output path.")
        return 1

    print("Configuration: PASS (Gnani selected; key, model, and catalog voice valid).")
    if args.check:
        print("No API request made; no audio file written.")
        return 0

    started = perf_counter()
    try:
        def report_diagnostics(metadata: dict[str, int | str | bool | None]) -> None:
            for key, value in metadata.items():
                print(f"Diagnostic {key}: {value}")

        mime_type, audio = asyncio.run(
            GnaniTTSProvider(
                settings.api_key, settings.tts_model, settings.tts_voice,
                diagnostics=report_diagnostics if args.diagnostic else None,
            )
            .synthesize(args.text, args.language)
        )
        latency_ms = (perf_counter() - started) * 1000
        if mime_type != "audio/wav":
            raise ValueError("Unexpected audio MIME type")
        duration = validate_tts_wav(audio)
        # Exclusive creation avoids silently replacing an earlier billable result.
        with args.output.open("xb") as output:
            output.write(audio)
    except SpeechProviderError as error:
        print(f"TTS error: {error.code}: {error}")
        print(f"Latency: {(perf_counter() - started) * 1000:.2f} ms")
        return 2
    except ValueError:
        print("TTS error: INVALID_AUDIO: returned audio is not a valid non-empty WAV.")
        return 2
    except FileExistsError:
        print("Save error: output file already exists; choose a new --output path.")
        return 2
    except OSError:
        print("Save error: output file could not be written.")
        return 2
    except Exception:
        print("TTS error: UNEXPECTED: local smoke test failed.")
        return 2

    print(f"Latency: {latency_ms:.2f} ms")
    print(f"Audio: PASS (audio/wav; {duration:.2f} s; {len(audio)} bytes).")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
