"""
OpenAI client singleton — GPT-4o for CAPTCHA vision solving.
Reads OPENAI_API_KEY from environment (set via .env).
"""
from __future__ import annotations
import os
import logging
from openai import AsyncOpenAI

log = logging.getLogger("ai.client")

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Add it to your .env file or environment."
            )
        _client = AsyncOpenAI(api_key=api_key)
        log.info("OpenAI client initialised (model: gpt-4o)")
    return _client
