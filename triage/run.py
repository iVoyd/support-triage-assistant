"""`triage run`: pick untriaged issues, retrieve docs, call the model, store records."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from .config import Config
from .docs import Retriever, load_chunks
from .llm import TriageResult, triage_issue
from .prompt import PROMPT_VERSION, build_request, retrieval_query


def pick_issues(
    conn: sqlite3.Connection, repo: str, limit: int, numbers: list[int] | None = None
) -> list[dict]:
    if numbers:
        marks = ",".join("?" * len(numbers))
        rows = conn.execute(
            f"SELECT * FROM issues WHERE repo = ? AND number IN ({marks}) ORDER BY number DESC",
            (repo, *numbers),
        )
    else:
        rows = conn.execute(
            """SELECT * FROM issues i
               WHERE repo = ? AND NOT EXISTS (
                   SELECT 1 FROM triage t WHERE t.repo = i.repo AND t.number = i.number)
               ORDER BY created_at DESC, number DESC LIMIT ?""",
            (repo, limit),
        )
    return [dict(r) for r in rows]


def save_result(
    conn: sqlite3.Connection,
    cfg: Config,
    issue: dict,
    urls: list[str],
    result: TriageResult,
) -> int:
    out = result.output or {}
    cur = conn.execute(
        """INSERT INTO triage (repo, number, model, prompt_version, type, area, severity,
               needs_engineer, needs_engineer_reason, summary, draft_reply, confidence,
               retrieved_urls, valid, errors, raw_output, input_tokens, output_tokens,
               cache_write_tokens, cache_read_tokens, cost_usd, latency_ms, api_calls,
               created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cfg.repo,
            issue["number"],
            cfg.model,
            PROMPT_VERSION,
            out.get("type"),
            out.get("area"),
            out.get("severity"),
            None if out.get("needs_engineer") is None else int(out["needs_engineer"]),
            out.get("needs_engineer_reason"),
            out.get("summary"),
            out.get("draft_reply"),
            out.get("confidence"),
            json.dumps(urls),
            int(result.valid),
            json.dumps(result.errors),
            json.dumps(result.output) if result.output is not None else None,
            result.usage.input_tokens,
            result.usage.output_tokens,
            result.usage.cache_write_tokens,
            result.usage.cache_read_tokens,
            result.usage.cost(cfg.price_for(cfg.model)),
            result.latency_ms,
            result.usage.api_calls,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return cur.lastrowid


def run_triage(
    conn: sqlite3.Connection,
    cfg: Config,
    client,
    limit: int,
    numbers: list[int] | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    chunks = load_chunks(conn, cfg.docs_source)
    if not chunks:
        log("warning: no doc chunks indexed; run `triage docs` first. Drafts will cite nothing.")
    retriever = Retriever(chunks)
    issues = pick_issues(conn, cfg.repo, limit, numbers)
    totals = {"issues": 0, "valid": 0, "cost_usd": 0.0, "latency_ms": 0, "cache_read_tokens": 0}
    for issue in issues:
        hits = [c for c, _ in retriever.search(retrieval_query(issue), cfg.top_k)]
        urls = [c.url for c in hits]
        request = build_request(
            repo=cfg.repo,
            model=cfg.model,
            effort=cfg.effort,
            max_tokens=cfg.max_tokens,
            areas=cfg.areas,
            issue=issue,
            chunks=hits,
        )
        result = triage_issue(client, request, list(cfg.areas), urls)
        save_result(conn, cfg, issue, urls, result)
        cost = result.usage.cost(cfg.price_for(cfg.model)) or 0.0
        totals["issues"] += 1
        totals["valid"] += int(result.valid)
        totals["cost_usd"] += cost
        totals["latency_ms"] += result.latency_ms
        totals["cache_read_tokens"] += result.usage.cache_read_tokens
        out = result.output or {}
        status = "ok" if result.valid else "INVALID: " + "; ".join(result.errors)
        log(
            f"#{issue['number']} {out.get('type')}/{out.get('area')}/{out.get('severity')} "
            f"eng={out.get('needs_engineer')} ${cost:.5f} {result.latency_ms}ms "
            f"cache_read={result.usage.cache_read_tokens} {status}"
        )
    return totals
