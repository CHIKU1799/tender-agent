"""Shared utilities: retry decorator, title parser, date helpers."""
from __future__ import annotations
import asyncio
import functools
import random
import re
from datetime import datetime
from typing import Callable, TypeVar

T = TypeVar("T")


def retry_async(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    jitter: float = 1.0,
    exceptions: tuple = (Exception,),
):
    """Decorator — exponential backoff with jitter for async functions."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        raise
                    delay = (base_delay ** attempt) + random.uniform(0, jitter)
                    await asyncio.sleep(delay)
        return wrapper
    return decorator


def parse_title_cell(raw: str) -> dict:
    """
    Parse the combined [Title][RefNo][TenderID] cell used on NIC GePNIC portals.
    Example: '[RUNWAY REPAIR][CEAFU Token-24/2025-26][2026_MES_757705_1]'
    """
    parts = re.findall(r"\[([^\[\]]+)\]", raw)
    tender_id = ref_number = title = ""

    if parts:
        for i in range(len(parts) - 1, -1, -1):
            if re.match(r"\d{4}_[A-Z_]+_\d+_\d+", parts[i]):
                tender_id  = parts[i]
                ref_number = parts[i - 1] if i > 0 else ""
                title      = parts[i - 2] if i > 1 else (parts[0] if parts else "")
                break
        if not tender_id:
            title = parts[0] if parts else raw
    else:
        title = raw.strip()

    return {
        "title":      title.strip(),
        "ref_number": ref_number.strip(),
        "tender_id":  tender_id.strip(),
    }


def safe_get(kv: dict, *keys: str) -> str:
    """Try multiple key names; return first non-empty match."""
    for key in keys:
        val = kv.get(key, "")
        if val:
            return val
    return ""


def now_iso() -> str:
    return datetime.utcnow().isoformat()
