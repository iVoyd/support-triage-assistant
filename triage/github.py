"""Fetch issues and their first comment from the GitHub REST API (read-only GETs).

Resumable: issues are upserted page by page, and first comments are fetched only for
issues that still need one, committing after each, so an interrupted run picks up where
it stopped.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

API = "https://api.github.com"


class RateLimited(Exception):
    """Raised when GitHub asks us to wait longer than we are willing to."""

    def __init__(self, wait_seconds: float):
        self.wait_seconds = wait_seconds
        super().__init__(f"GitHub rate limit hit; resets in {wait_seconds:.0f}s. Re-run later.")


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        max_wait: float = 90.0,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "support-triage-assistant",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=API, headers=headers, timeout=30, transport=transport)
        self.sleep = sleep
        self.now = now
        self.max_wait = max_wait
        self.requests = 0

    def _wait_seconds(self, r: httpx.Response) -> float | None:
        """How long GitHub wants us to wait, or None if this is not a rate-limit response."""
        if r.status_code not in (403, 429):
            return None
        if "retry-after" in r.headers:
            return float(r.headers["retry-after"])
        if r.headers.get("x-ratelimit-remaining") == "0":
            reset = float(r.headers.get("x-ratelimit-reset", self.now() + 60))
            return max(reset - self.now(), 0) + 1
        return None

    def get(self, path: str, params: dict | None = None):
        for _ in range(3):
            r = self.http.get(path, params=params)
            self.requests += 1
            wait = self._wait_seconds(r)
            if wait is not None:
                if wait > self.max_wait:
                    raise RateLimited(wait)
                self.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        raise RateLimited(self.max_wait)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def upsert_issue(conn: sqlite3.Connection, repo: str, item: dict) -> None:
    conn.execute(
        """
        INSERT INTO issues (repo, number, title, body, state, state_reason, labels, author,
                            html_url, created_at, updated_at, closed_at, comments_count,
                            fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo, number) DO UPDATE SET
            title = excluded.title, body = excluded.body, state = excluded.state,
            state_reason = excluded.state_reason, labels = excluded.labels,
            updated_at = excluded.updated_at, closed_at = excluded.closed_at,
            comments_count = excluded.comments_count, fetched_at = excluded.fetched_at,
            -- re-check the first comment only if we never got one
            first_comment_fetched = CASE WHEN issues.first_comment IS NULL
                                         THEN 0 ELSE 1 END
        """,
        (
            repo,
            item["number"],
            item["title"],
            item.get("body") or "",
            item.get("state"),
            item.get("state_reason"),
            json.dumps([lb["name"] for lb in item.get("labels", [])]),
            (item.get("user") or {}).get("login"),
            item.get("html_url"),
            item.get("created_at"),
            item.get("updated_at"),
            item.get("closed_at"),
            item.get("comments", 0),
            _now(),
        ),
    )


def fetch_issues(
    gh: GitHubClient,
    conn: sqlite3.Connection,
    repo: str,
    limit: int,
    log: Callable[[str], None] = print,
) -> dict:
    """Store the `limit` newest issues (pull requests skipped), then their first comments."""
    stored = 0
    page = 1
    while stored < limit:
        items = gh.get(
            f"/repos/{repo}/issues",
            params={
                "state": "all",
                "sort": "created",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        if not items:
            break
        for item in items:
            if "pull_request" in item:
                continue
            upsert_issue(conn, repo, item)
            stored += 1
            if stored >= limit:
                break
        conn.commit()
        log(f"page {page}: {stored} issues stored")
        page += 1

    pending = conn.execute(
        """SELECT number FROM issues
           WHERE repo = ? AND comments_count > 0 AND first_comment_fetched = 0
           ORDER BY number DESC""",
        (repo,),
    ).fetchall()
    comments = 0
    for row in pending:
        data = gh.get(f"/repos/{repo}/issues/{row['number']}/comments", params={"per_page": 1})
        first = data[0] if data else None
        conn.execute(
            """UPDATE issues SET first_comment = ?, first_comment_author = ?,
                                 first_comment_fetched = 1
               WHERE repo = ? AND number = ?""",
            (
                first["body"] if first else None,
                (first.get("user") or {}).get("login") if first else None,
                repo,
                row["number"],
            ),
        )
        conn.commit()
        comments += 1
    return {"issues": stored, "comments": comments, "requests": gh.requests}


def search_closed_duplicates(gh: GitHubClient, repo: str, limit: int) -> list[dict]:
    """Issues closed with the 'duplicate' reason, newest first (one search request)."""
    data = gh.get(
        "/search/issues",
        params={
            "q": f"repo:{repo} is:issue reason:duplicate",
            "sort": "created",
            "order": "desc",
            "per_page": min(limit, 100),
        },
    )
    return data.get("items", [])[:limit]


def get_issue(gh: GitHubClient, repo: str, number: int) -> dict:
    return gh.get(f"/repos/{repo}/issues/{number}")


def get_comments(gh: GitHubClient, repo: str, number: int) -> list[dict]:
    return gh.get(f"/repos/{repo}/issues/{number}/comments", params={"per_page": 100})
