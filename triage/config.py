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
    )
