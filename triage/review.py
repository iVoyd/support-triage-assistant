"""`triage review`: Theo approves, edits or rejects each draft. Nothing is posted anywhere.

Decisions are stored in the `reviews` table with the original and final draft and a
word-level edit distance, so the scorecard can report the approval rate and how much
approved drafts had to change.
"""

from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    triage_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    decision TEXT NOT NULL,          -- approve / edit / reject
    original_draft TEXT,
    final_draft TEXT,                -- NULL when rejected
    edit_distance REAL,              -- 0 = unchanged, 1 = completely rewritten; NULL if rejected
    reason TEXT,                     -- why it was rejected (optional for edits)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_triage ON reviews(triage_id);
"""

HELP = """\
  a  approve the draft as it is
  e  edit the draft (opens $EDITOR, or type it here), then it counts as approved with edits
  r  reject the draft (you are asked why)
  s  skip for now      q  quit (every decision so far is saved)"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# ---------- edit distance ----------


def word_edit_distance(a: str, b: str) -> float:
    """Word-level Levenshtein distance divided by the longer length: 0.0 same, 1.0 all new."""
    x, y = (a or "").split(), (b or "").split()
    if not x and not y:
        return 0.0
    prev = list(range(len(y) + 1))
    for i, wx in enumerate(x, 1):
        cur = [i]
        for j, wy in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wx != wy)))
        prev = cur
    return prev[-1] / max(len(x), len(y))


# ---------- editing ----------


def edit_in_editor(text: str) -> str:
    """Open $EDITOR on the draft; without one, read lines until a line with just '.'."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        print("  No $EDITOR set. Type the new draft; finish with a line containing only '.'")
        lines = []
        while (line := input()) != ".":
            lines.append(line)
        return "\n".join(lines)
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        f.write(text)
        name = f.name
    try:
        subprocess.run([*shlex.split(editor), name], check=True)
        return Path(name).read_text().strip()
    finally:
        os.unlink(name)


# ---------- picking and showing ----------


def pick_unreviewed(
    conn: sqlite3.Connection, cfg: Config, limit: int, numbers: list[int] | None = None
) -> list[dict]:
    """Latest valid record per issue for the current model and prompt, not yet reviewed."""
    ensure_table(conn)
    filter_numbers = ""
    params: list = [cfg.repo, cfg.model, cfg.prompt_version]
    if numbers:
        filter_numbers = f" AND t.number IN ({','.join('?' * len(numbers))})"
        params += numbers
    rows = conn.execute(
        f"""SELECT t.*, i.title, i.html_url, i.body FROM triage t
            JOIN issues i ON i.repo = t.repo AND i.number = t.number
            WHERE t.repo = ? AND t.model = ? AND t.prompt_version = ? AND t.valid = 1
              AND t.id = (SELECT MAX(id) FROM triage t2 WHERE t2.repo = t.repo
                          AND t2.number = t.number AND t2.model = t.model
                          AND t2.prompt_version = t.prompt_version AND t2.valid = 1)
              AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.triage_id = t.id)
              {filter_numbers}
            ORDER BY t.number DESC LIMIT ?""",
        (*params, limit),
    )
    return [dict(r) for r in rows]


def show(rec: dict, duplicates: list[dict], out: Callable[[str], None]) -> None:
    out("")
    out(f"#{rec['number']} {rec['title']}")
    out(f"  {rec['html_url']}")
    out(
        f"  type={rec['type']} area={rec['area']} severity={rec['severity']} "
        f"needs_engineer={'yes' if rec['needs_engineer'] else 'no'} "
        f"confidence={rec['confidence']}"
    )
    out(f"  engineer: {rec['needs_engineer_reason']}")
    out(f"  summary: {rec['summary']}")
    retrieved = json.loads(rec["retrieved_urls"] or "[]")
    relevant = set(json.loads(rec["relevant_docs"])) if rec.get("relevant_docs") else set()
    if retrieved:
        out("  docs retrieved (* = model judged relevant):")
        for url in retrieved:
            out(f"    {'*' if url in relevant else ' '} {url}")
    else:
        out("  docs retrieved: none above min_score")
    same = [d for d in duplicates if d["is_duplicate"] == 1]
    if same:
        out(
            "  possible duplicate: " + "; ".join(f"#{d['candidate']} ({d['reason']})" for d in same)
        )
    out("  draft reply:")
    for line in (rec["draft_reply"] or "").splitlines() or [""]:
        out(f"    | {line}")


# ---------- the loop ----------


def save(
    conn: sqlite3.Connection,
    cfg: Config,
    rec: dict,
    decision: str,
    final: str | None,
    reason: str | None,
) -> None:
    distance = None if decision == "reject" else word_edit_distance(rec["draft_reply"], final)
    conn.execute(
        """INSERT INTO reviews (repo, number, triage_id, model, prompt_version, decision,
               original_draft, final_draft, edit_distance, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cfg.repo,
            rec["number"],
            rec["id"],
            rec["model"],
            rec["prompt_version"],
            decision,
            rec["draft_reply"],
            final,
            distance,
            reason,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def review_loop(
    conn: sqlite3.Connection,
    cfg: Config,
    limit: int,
    numbers: list[int] | None = None,
    ask: Callable[[str], str] | None = None,
    edit: Callable[[str], str] = edit_in_editor,
    out: Callable[[str], None] = print,
) -> dict:
    from .duplicates import describe

    ask = ask or input
    ensure_table(conn)
    todo = pick_unreviewed(conn, cfg, limit, numbers)
    counts = {"approve": 0, "edit": 0, "reject": 0, "skipped": 0}
    out(f"{len(todo)} drafts to review ({cfg.model}, prompt {cfg.prompt_version}).")
    out(HELP)
    for rec in todo:
        show(rec, describe(conn, cfg.repo, rec["number"], cfg.model), out)
        while True:
            try:
                answer = ask("  [a/e/r/s/q] > ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                answer = "q"
            if answer == "q":
                return counts
            if answer == "s":
                counts["skipped"] += 1
                break
            if answer == "a":
                save(conn, cfg, rec, "approve", rec["draft_reply"], None)
            elif answer == "e":
                final = edit(rec["draft_reply"] or "").strip()
                if not final:
                    out("  empty draft: not saved. Use r to reject instead.")
                    continue
                if final == (rec["draft_reply"] or "").strip():
                    out("  no change: saved as approved.")
                    answer = "a"
                    save(conn, cfg, rec, "approve", final, None)
                else:
                    note = ask("  why the edit? (optional) > ").strip()
                    save(conn, cfg, rec, "edit", final, note or None)
            elif answer == "r":
                reason = ask("  why reject? > ").strip()
                save(conn, cfg, rec, "reject", None, reason or None)
            else:
                out(HELP)
                continue
            counts[{"a": "approve", "e": "edit", "r": "reject"}[answer]] += 1
            break
    return counts


# ---------- reporting ----------


def review_stats(conn: sqlite3.Connection, repo: str, model: str, version: str) -> dict:
    """Approval rate and edit distance for the latest decision on each reviewed record."""
    ensure_table(conn)
    rows = conn.execute(
        """SELECT decision, edit_distance FROM reviews r
           WHERE repo = ? AND model = ? AND prompt_version = ?
             AND id = (SELECT MAX(id) FROM reviews r2 WHERE r2.triage_id = r.triage_id)""",
        (repo, model, version),
    ).fetchall()
    n = len(rows)
    by = {d: [r for r in rows if r["decision"] == d] for d in ("approve", "edit", "reject")}
    kept = by["approve"] + by["edit"]
    edited = [r["edit_distance"] for r in by["edit"]]
    return {
        "n": n,
        "approved": len(by["approve"]),
        "edited": len(by["edit"]),
        "rejected": len(by["reject"]),
        # usable = posted as is or after edits; approval = posted unchanged
        "approval_rate": len(by["approve"]) / n if n else None,
        "usable_rate": len(kept) / n if n else None,
        "mean_edit_distance_edited": sum(edited) / len(edited) if edited else None,
        "mean_edit_distance_usable": (
            sum(r["edit_distance"] or 0 for r in kept) / len(kept) if kept else None
        ),
    }


def export_reviews(conn: sqlite3.Connection, repo: str, path: Path) -> int:
    """Write every decision to a CSV, so reviews can be committed with the scorecard."""
    import csv

    ensure_table(conn)
    rows = conn.execute(
        """SELECT number, model, prompt_version, decision, edit_distance, reason,
                  original_draft, final_draft, created_at
           FROM reviews WHERE repo = ? ORDER BY id""",
        (repo,),
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(rows[0].keys() if rows else ["number"])
        for r in rows:
            w.writerow(list(r))
    return len(rows)
