"""Load triage.toml into a small dataclass."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Prices:
    """USD per million tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


@dataclass
class Config:
    repo: str
    docs_source: str
    docs_subdir: str
    docs_url_base: str
    model: str
    effort: str
    top_k: int
    max_tokens: int
    areas: dict[str, str]
    prices: dict[str, Prices] = field(default_factory=dict)
    prompt_version: str = "v4"
    # retrieval: drop doc chunks whose BM25 score is below this (0 = keep all top_k)
    min_score: float = 0.0
    # duplicates: how many TF-IDF candidates, and the cosine needed before asking the model
    dup_candidates: int = 5
    dup_min_similarity: float = 0.2
    # GitHub label name -> our value, used to seed eval/labels.csv
    label_types: dict[str, str] = field(default_factory=dict)
    label_areas: dict[str, str] = field(default_factory=dict)
    # `triage label --suggest`: a larger model than the one being evaluated
    suggest_model: str = "claude-sonnet-5-5"
    suggest_effort: str = "medium"

    def price_for(self, model: str) -> Prices | None:
        return self.prices.get(model)


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    docs = raw.get("docs", {})
    tri = raw.get("triage", {})
    areas = tri.get("areas") or {"other": "Anything else"}
    if "other" not in areas:
        areas = {**areas, "other": "Anything that fits none of the above"}
    prices = {name: Prices(**vals) for name, vals in raw.get("prices", {}).items()}
    dups = raw.get("duplicates", {})
    labels = raw.get("labels", {})
    return Config(
        repo=raw["project"]["repo"],
        docs_source=docs.get("source", ""),
        docs_subdir=docs.get("subdir", ""),
        docs_url_base=docs.get("url_base", "").rstrip("/"),
        model=tri.get("model", "claude-haiku-5-5"),
        effort=tri.get("effort", "low"),
        top_k=int(tri.get("top_k", 5)),
        max_tokens=int(tri.get("max_tokens", 4000)),
        areas=dict(areas),
        prices=prices,
        prompt_version=tri.get("prompt_version", "v4"),
        min_score=float(tri.get("min_score", 0.0)),
        dup_candidates=int(dups.get("candidates", 5)),
        dup_min_similarity=float(dups.get("min_similarity", 0.2)),
        label_types=dict(labels.get("types", {})),
        label_areas=dict(labels.get("areas", {})),
        suggest_model=labels.get("suggest_model", "claude-sonnet-5-5"),
        suggest_effort=labels.get("suggest_effort", "medium"),
    )
