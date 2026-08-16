"""AI provider adapters (behind ``ModelGatewayPort``). Reference build ships a mock (FR-AI-001).

A real provider SDK (OpenAI/Anthropic/Bedrock/…) is a straight adapter behind the same port; the
mock here uses NO external API so tests are deterministic and the red-team corpus is reproducible.
"""

from __future__ import annotations

from .mock_provider import DeterministicMockProvider, MockProviderConfig

__all__ = ["DeterministicMockProvider", "MockProviderConfig"]
