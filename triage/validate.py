"""Check the model's tool output against the rules the schema alone cannot express."""

from __future__ import annotations

import re

from .prompt import SEVERITIES, TYPES

URL = re.compile(r"https?://[^\s)\]>\"'`]+")
SENTENCE_END = re.compile(r"[.!?](?:\s+|$)")
FIELDS = [
    "type",
    "area",
    "severity",
    "needs_engineer",
    "needs_engineer_reason",
    "summary",
    "draft_reply",
    "confidence",
]


def count_sentences(text: str) -> int:
    text = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", "x", text.strip())
    text = re.sub(r"\d\.\d", "x", text)  # version numbers like 2.45.1
    if not text:
        return 0
    n = len(SENTENCE_END.findall(text))
    return n if text[-1] in ".!?" else n + 1


def urls_in(text: str) -> list[str]:
    return [u.rstrip(".,;:") for u in URL.findall(text or "")]


def validate_output(data: dict, areas: list[str], allowed_urls: list[str]) -> list[str]:
    """Return a list of human-readable problems. Empty list means valid."""
    errors = []
    missing = [f for f in FIELDS if f not in data]
    if missing:
        return [f"missing fields: {', '.join(missing)}"]
    if data["type"] not in TYPES:
        errors.append(f"type must be one of {TYPES}, got {data['type']!r}")
    if data["area"] not in areas:
        errors.append(f"area must be one of {areas}, got {data['area']!r}")
    if data["severity"] not in SEVERITIES:
        errors.append(f"severity must be one of {SEVERITIES}, got {data['severity']!r}")
    if not isinstance(data["needs_engineer"], bool):
        errors.append("needs_engineer must be true or false")
    if not str(data["needs_engineer_reason"]).strip():
        errors.append("needs_engineer_reason must not be empty")
    conf = data["confidence"]
    if isinstance(conf, bool) or not isinstance(conf, int | float) or not 0 <= conf <= 1:
        errors.append(f"confidence must be a number between 0 and 1, got {conf!r}")
    summary = str(data["summary"]).strip()
    if not summary:
        errors.append("summary must not be empty")
    elif count_sentences(summary) > 3:
        errors.append(f"summary must be at most 3 sentences, got {count_sentences(summary)}")
    if not str(data["draft_reply"]).strip():
        errors.append("draft_reply must not be empty")
    allowed = {u.rstrip("/") for u in allowed_urls}
    bad = [u for u in urls_in(data["draft_reply"]) if u.rstrip("/") not in allowed]
    if bad:
        errors.append(
            "draft_reply may only cite documentation URLs given with the issue; "
            f"not allowed: {', '.join(bad)}"
        )
    return errors
