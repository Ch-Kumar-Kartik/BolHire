"""Explicit, single-request Gemini text smoke test with an offline preflight."""

import argparse
import asyncio
import os
from time import perf_counter

from app.models import ConversationMessage
from app.providers.gemini import GeminiLLMProvider, GeminiProviderError
from app.providers.llm_config import LLMSettings


async def _run_once(settings: LLMSettings, text: str, language: str) -> str:
    provider = GeminiLLMProvider(settings)
    try:
        return await provider.generate(
            [ConversationMessage(role="user", content=text)], language
        )
    finally:
        await provider.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Gemini configuration or send one text request.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Validate configuration without an API request.")
    action.add_argument("--run", action="store_true", help="Send exactly one Gemini request.")
    parser.add_argument("--confirm-billable", action="store_true", help="Required with --run after explicit approval.")
    parser.add_argument("--text", default="Hello, what can BolHire help me with?")
    parser.add_argument("--language", choices=("en-IN", "hi-IN"), default="en-IN")
    args = parser.parse_args(argv)

    if args.run and not args.confirm_billable:
        print("Preflight FAIL: --run requires --confirm-billable.")
        return 1
    if not args.text.strip() or len(args.text) > 4000:
        print("Preflight FAIL: text must contain 1 to 4000 characters.")
        return 1
    try:
        settings = LLMSettings.from_env(os.environ)
        if settings.provider != "gemini":
            raise ValueError("LLM_PROVIDER must be gemini for this smoke test.")
    except ValueError as error:
        print(f"Preflight FAIL: {error}")
        return 1

    print(f"Configuration: PASS (Gemini selected; model {settings.model}; key and limits valid).")
    if args.check:
        print("No API request made.")
        return 0

    started = perf_counter()
    try:
        reply = asyncio.run(_run_once(settings, args.text, args.language))
    except GeminiProviderError as error:
        print(f"LLM error: {error.code}: {error}")
        print(f"Latency: {(perf_counter() - started) * 1000:.2f} ms")
        return 2
    except Exception:
        print("LLM error: UNEXPECTED: local smoke test failed.")
        print(f"Latency: {(perf_counter() - started) * 1000:.2f} ms")
        return 2
    print(f"Reply: {reply}")
    print(f"Latency: {(perf_counter() - started) * 1000:.2f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
