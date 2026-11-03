"""SQLite storage. One file holds issues, doc chunks and triage records."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    state TEXT,
    state_reason TEXT,
    labels TEXT,                 -- JSON list of label names
    author TEXT,
    html_url TEXT,
    created_at TEXT,
    updated_at TEXT,
    closed_at TEXT,
    comments_count INTEGER DEFAULT 0,
    first_comment TEXT,
    first_comment_author TEXT,
    first_comment_fetched INTEGER DEFAULT 0,
    fetched_at TEXT,
    PRIMARY KEY (repo, number)
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    heading TEXT,
    url TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_chunks_source ON doc_chunks(source);

CREATE TABLE IF NOT EXISTS triage (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    type TEXT,
    area TEXT,
    severity TEXT,
    needs_engineer INTEGER,
    needs_engineer_reason TEXT,
    summary TEXT,
    draft_reply TEXT,
    confidence REAL,
    retrieved_urls TEXT,         -- JSON list
    retrieved_scores TEXT,       -- JSON list of BM25 scores, same order (v2+)
    relevant_docs TEXT,          -- JSON list of retrieved URLs the model judged relevant (v2+)
    valid INTEGER NOT NULL,      -- 1 if output passed validation
    errors TEXT,                 -- JSON list of validation errors
    raw_output TEXT,             -- JSON tool input as returned by the model
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_write_tokens INTEGER,
    cache_read_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    api_calls INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS triage_issue ON triage(repo, number);

CREATE TABLE IF NOT EXISTS duplicates (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    candidate INTEGER NOT NULL,
    similarity REAL NOT NULL,    -- TF-IDF cosine
    is_duplicate INTEGER,        -- model verdict; NULL if the model was not asked
    reason TEXT,
    model TEXT,
    run_id TEXT NOT NULL,        -- groups the rows of one check of one issue
    cost_usd REAL,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS duplicates_issue ON duplicates(repo, number);
"""

# Columns added after Milestone 1; ALTER old databases in place.
MIGRATIONS = {
    "triage": {"retrieved_scores": "TEXT", "relevant_docs": "TEXT"},
}


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, columns in MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.commit()
    return conn
