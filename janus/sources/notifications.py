from __future__ import annotations

import re
from typing import Optional, Tuple

from ..models import Signal
from ..normalize import from_text

_TRIGGER_WORDS = re.compile(r"new trade|new idea|trade posted|recommendation|alert|update", re.I)


def parse_notification(text: str, source: str = "notification") -> Tuple[str, Optional[Signal]]:
    """A UNIVEST push often carries no details (just 'new trade posted').

    Returns ("signal", Signal) when the text is rich enough to parse, otherwise
    ("trigger", None) to wake the web connector immediately.
    """
    text = (text or "").strip()
    if not text:
        return "trigger", None
    has_levels = bool(re.search(r"\bSL\b|\bSTOP\b|\bTGT\b|\bTARGET\b", text, re.I))
    sig = from_text(text, source=source) if has_levels else None
    if sig is not None:
        return "signal", sig
    if _TRIGGER_WORDS.search(text):
        return "trigger", None
    return "trigger", None
