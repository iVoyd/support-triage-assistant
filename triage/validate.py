"""Check the model's tool output against the rules the schema alone cannot express."""

from __future__ import annotations

import re

from .prompt import SEVERITIES, TYPES

URL = re.compile(r"https?://[^\s)\]>\"'`]+")
SENTENCE_END = re.compile(r"[.!?](?:\s+|$)")
# Things a draft must not claim: nobody has acted on the issue when the draft is written.
CLAIMED_ACTION = re.compile(
    r"\b(?:I|we)(?:'ve| have)? (?:passed|forwarded|escalated|flagged|reproduced|confirmed|"
    r"shared|raised|reported)\b"
    r"|\b(?:I'm|I am|we're|we are) (?:passing|forwarding|escalating|flagging|looking into)\b"
    r"|\bwe can reproduce\b"
    r"|\bwe(?:'ll| will) (?:look into|investigate|fix|follow up)\b",
    re.IGNORECASE,
)
# v4 only (so older versions stay reproducible): more claimed actions, talk about the
# model's own inputs, and pointing at a link without giving it.
CLAIMED_ACTION_V4 = re.compile(
    CLAIMED_ACTION.pattern
    + r"|\b(?:I|we)(?:'ve| have)? (?:labell?ed|tagged|assigned|closed|marked|moved|triaged)\b"
    + r"|\b(?:I|we)(?:'ve| have) added (?:a |the )?label",
    re.IGNORECASE,
)
INPUT_TALK = re.compile(
    r"\b(?:docs|documentation|excerpts?|sources?) (?:provided|given|I (?:have|was given|can see)"
    r"|available to me)\b"
    r"|\b(?:the |these )?(?:provided|given) (?:docs|documentation|excerpts?)\b"
    r"|\bexcerpts?\b"
    r"|\bI (?:don't|do not) have (?:any )?(?:docs|documentation)\b",
    re.IGNORECASE,
)
DANGLING_LINK = re.compile(
    r"\blink(?:ed)? (?:below|above)\b|\bthe following (?:link|guide|page|docs?)\b"
    r"|\b(?:see|check|read) (?:this|that) (?:guide|page|doc|link)\b"
    r"|\bhere(?:'s| is) (?:the|a) (?:link|guide|doc)\b|\blinked (?:guide|page|doc)",
    re.IGNORECASE,
)
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


def _norm(url: str) -> str:
    return url.strip().rstrip("/")


def validate_output(
    data: dict,
    areas: list[str],
    allowed_urls: list[str],
    version: str = "v2",
    duplicate: int | None = None,
) -> list[str]:
    """Return a list of human-readable problems. Empty list means valid.

    allowed_urls are the retrieved doc URLs. From v2 on, the model also lists which of them
    are relevant, and the draft may cite only those.
    """
    errors = []
    fields = FIELDS if version == "v1" else [*FIELDS, "relevant_docs"]
    missing = [f for f in fields if f not in data]
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
    v4 = version not in ("v1", "v2", "v3")
    draft = str(data["draft_reply"])
    if version != "v1":
        pattern = CLAIMED_ACTION_V4 if v4 else CLAIMED_ACTION
        claims = sorted({m.group(0) for m in pattern.finditer(draft)})
        if claims:
            errors.append(
                "draft_reply claims an action nobody has taken (" + "; ".join(claims) + "); "
                "describe only what is true now"
            )
    if v4:
        talk = sorted({m.group(0) for m in INPUT_TALK.finditer(draft)})
        if talk:
            errors.append(
                "draft_reply talks about your inputs (" + "; ".join(talk) + "); write about "
                "the reporter's problem and cite docs by URL only"
            )
        if DANGLING_LINK.search(draft) and not urls_in(draft):
            errors.append("draft_reply refers to a link or guide but gives no URL")
        if duplicate and not re.search(rf"#?\b{duplicate}\b", draft):
            errors.append(
                f"a likely duplicate (#{duplicate}) was given; say so in draft_reply and "
                "name it by number"
            )
    allowed = {_norm(u) for u in allowed_urls}
    if version != "v1":
        relevant = data["relevant_docs"]
        if not isinstance(relevant, list) or not all(isinstance(u, str) for u in relevant):
            errors.append("relevant_docs must be a list of URLs")
            relevant = []
        unknown = [u for u in relevant if _norm(u) not in allowed]
        if unknown:
            errors.append(
                "relevant_docs may only list URLs of the documentation given with the issue; "
                f"not given: {', '.join(unknown)}"
            )
        allowed = {_norm(u) for u in relevant} & allowed
    bad = [u for u in urls_in(data["draft_reply"]) if _norm(u) not in allowed]
    if bad:
        errors.append(
            "draft_reply may only cite documentation URLs given with the issue"
            + ("" if version == "v1" else " and listed in relevant_docs")
            + "; "
            f"not allowed: {', '.join(bad)}"
        )
    return errors
