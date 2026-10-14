from collections.abc import Sequence
from typing import Protocol

from app.models import ConversationMessage


class LLMProvider(Protocol):
    async def generate(
        self, messages: Sequence[ConversationMessage], language: str
    ) -> str:
        """Generate one assistant reply from the full conversation context."""


class MockLLMProvider:
    """A deterministic local provider for pipeline development and tests."""

    async def generate(
        self, messages: Sequence[ConversationMessage], language: str
    ) -> str:
        latest_message = messages[-1]
        prior_message_count = len(messages) - 1
        return (
            f'Mock response [{language}]. Received: "{latest_message.content}". '
            f"Prior messages supplied: {prior_message_count}."
        )
