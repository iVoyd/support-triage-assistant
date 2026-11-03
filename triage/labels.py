"""The labelled evaluation set: eval/labels.csv.

`triage label --seed` picks ~60 issues and pre-fills what GitHub already says (mapped
labels, closed-as-duplicate links). `triage label` then walks through unconfirmed rows so
Theo can accept or fix each one in a few keystrokes. Every row is saved as soon as it is
answered, so quitting half-way loses nothing.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .config import Config
from .prompt import TYPES

COLUMNS = [
    "number",
    "title",
    "type",
    "area",
    "needs_engineer",  # yes / no / blank
    "duplicate_of",  # issue number, "?" (closed as duplicate, target unknown), or blank
    "doc_url",  # a docs page that answers the issue, "none", or blank (not labelled)
    "confirmed",  # yes once Theo has checked the row
    "seed",  # where the pre-filled values came from
    "notes",
    "changed",  # on confirm: suggested fields Theo changed ("none" if he kept them all)
    # hand: confirmed in the full manual flow; suggested: copied from the suggester and not
    # checked; spot-checked: a suggested row Theo checked in `triage label --spot-check`
    "source",
]
DUP_REF = re.compile(r"(?:duplicate of|dupe of|duplicates|same as|see)\s*#(\d+)", re.IGNORECASE)
HASH_REF = re.compile(r"(?<![\w/])#(\d+)\b")


# ---------- CSV ----------


def read_labels(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return [{c: (row.get(c) or "").strip() for c in COLUMNS} for row in csv.DictReader(f)]


def write_labels(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: -int(r["number"])):
            writer.writerow({c: row.get(c, "") for c in COLUMNS})
    tmp.replace(path)  # never leave a half-written file


# ---------- seeding ----------


def duplicate_target(texts: list[str], own_number: int) -> str:
    """Find "duplicate of #123" in the body or comments; fall back to a lone #ref."""
    for text in texts:
        m = DUP_REF.search(text or "")
        if m and int(m.group(1)) != own_number:
            return m.group(1)
    refs = {int(n) for t in texts for n in HASH_REF.findall(t or "")} - {own_number}
    return str(refs.pop()) if len(refs) == 1 else "?"


def seed_row(issue: dict, cfg: Config, duplicate_of: str = "") -> dict:
    labels = json.loads(issue.get("labels") or "[]")
    types = [cfg.label_types[lb] for lb in labels if lb in cfg.label_types]
    areas = [cfg.label_areas[lb] for lb in labels if lb in cfg.label_areas]
    seed = []
    if types or areas:
        seed.append(
            "github labels: "
            + ", ".join(lb for lb in labels if lb in cfg.label_types or lb in cfg.label_areas)
        )
    if duplicate_of:
        seed.append("closed as duplicate")
    return {
        "number": str(issue["number"]),
        "title": issue["title"][:120],
        "type": types[0] if types else "",
        "area": areas[0] if areas and areas[0] in cfg.areas else "",
        "needs_engineer": "",  # never seeded: GitHub has no label for it
        "duplicate_of": duplicate_of,
        "doc_url": "",
        "confirmed": "",
        "seed": "; ".join(seed),
        "notes": "",
    }


def fetch_duplicate_examples(
    gh, conn: sqlite3.Connection, cfg: Config, limit: int, log: Callable[[str], None] = print
) -> dict[int, str]:
    """Store recent closed-as-duplicate issues and their targets; return {number: target}."""
    from .github import (
        duplicate_targets,
        get_comments,
        get_issue,
        search_closed_duplicates,
        upsert_issue,
    )

    found = {}
    items = search_closed_duplicates(gh, cfg.repo, limit)
    # GitHub records the target on the close event (GraphQL only, needs a token)
    recorded = duplicate_targets(gh, cfg.repo, [i["number"] for i in items]) if gh.token else {}
    if items and not recorded:
        log(
            "note: could not read duplicate targets from GitHub (no token or query failed); "
            "falling back to 'duplicate of #N' comments"
        )
    for item in items:
        upsert_issue(conn, cfg.repo, item)
        if item["number"] in recorded:
            target = str(recorded[item["number"]])
        else:
            comments = get_comments(gh, cfg.repo, item["number"]) if item.get("comments") else []
            target = duplicate_target(
                [c.get("body") or "" for c in comments] + [item.get("body") or ""],
                item["number"],
            )
        if target != "?":
            have = conn.execute(
                "SELECT 1 FROM issues WHERE repo = ? AND number = ?", (cfg.repo, int(target))
            ).fetchone()
            if not have:
                upsert_issue(conn, cfg.repo, get_issue(gh, cfg.repo, int(target)))
        conn.commit()
        found[item["number"]] = target
        log(f"#{item['number']} closed as duplicate of {target if target != '?' else '(unknown)'}")
    return found


def select_issues(conn: sqlite3.Connection, cfg: Config, n: int, must: list[int]) -> list[dict]:
    """`must` first (duplicates, already-triaged), then issues with mapped labels, then newest."""
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM issues WHERE repo = ? ORDER BY created_at DESC, number DESC", (cfg.repo,)
        )
    ]
    by_number = {r["number"]: r for r in rows}
    mapped = set(cfg.label_types) | set(cfg.label_areas)
    has_mapped = [r for r in rows if mapped & set(json.loads(r.get("labels") or "[]"))]
    chosen, seen = [], set()
    for r in [*(by_number[m] for m in must if m in by_number), *has_mapped, *rows]:
        if r["number"] not in seen and len(chosen) < n:
            chosen.append(r)
            seen.add(r["number"])
    return chosen


def seed_labels(
    conn: sqlite3.Connection, cfg: Config, path: Path, n: int, duplicates: dict[int, str]
) -> tuple[int, int]:
    """Add seeded rows to labels.csv.

    Existing rows are kept. The only change to them: an unconfirmed row whose duplicate
    target was unknown ("?" or blank) gets the target if one was found now.
    """
    existing = read_labels(path)
    for row in existing:
        target = duplicates.get(int(row["number"]), "")
        if row["confirmed"] != "yes" and row["duplicate_of"] in ("?", "") and target.isdigit():
            row["duplicate_of"] = target
    have = {int(r["number"]) for r in existing}
    triaged = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT number FROM triage WHERE repo = ? ORDER BY number DESC", (cfg.repo,)
        )
    ]
    targets = [int(t) for t in duplicates.values() if t.isdigit()]
    must = [*duplicates, *targets, *triaged]
    new = []
    for issue in select_issues(conn, cfg, n + len(have), must):
        if len(existing) + len(new) >= n:  # already at the target size: add nothing
            break
        if issue["number"] in have:
            continue
        dup = duplicates.get(issue["number"], "")
        if not dup and issue.get("state_reason") == "duplicate":
            dup = "?"
        new.append(seed_row(issue, cfg, dup))
    write_labels(path, existing + new)
    return len(existing), len(new)


# ---------- interactive confirmation ----------

HELP = """\
  Enter      accept the values shown (type, area and engineer must be set)
  t=b a=auth e=y d=123 u=<url|none> n=<note>   set fields, then confirm
             t: b(ug) q(uestion) f(eature) d(ocs) o(ther); a: any unique prefix of an area
             e: y/n; d: duplicate issue number or - to clear; u: doc URL, none, or -
  s          skip this row for now      q   quit (everything answered so far is saved)"""


def apply_edits(row: dict, text: str, areas: list[str]) -> list[str]:
    """Apply "t=bug a=auth ..." to row in place; return problems (row unchanged on problems)."""
    updates, errors = {}, []
    for part in re.findall(r"(\w)=(\"[^\"]*\"|\S+)", text):
        key, value = part[0], part[1].strip('"')
        if key == "t":
            match = [t for t in TYPES if t.startswith(value.lower())]
            if len(match) == 1:
                updates["type"] = match[0]
            else:
                errors.append(f"type {value!r}: pick one of {', '.join(TYPES)}")
        elif key == "a":
            match = [a for a in areas if a == value] or [a for a in areas if a.startswith(value)]
            if len(match) == 1:
                updates["area"] = match[0]
            else:
                errors.append(f"area {value!r}: {'ambiguous' if match else 'unknown'}")
        elif key == "e":
            if value.lower() in ("y", "yes", "n", "no"):
                updates["needs_engineer"] = "yes" if value.lower().startswith("y") else "no"
            else:
                errors.append("engineer: use y or n")
        elif key == "d":
            if value == "-" or value.lstrip("#").isdigit():
                updates["duplicate_of"] = "" if value == "-" else value.lstrip("#")
            else:
                errors.append("duplicate: an issue number or -")
        elif key == "u":
            updates["doc_url"] = "" if value == "-" else value
        elif key == "n":
            updates["notes"] = value
        else:
            errors.append(f"unknown field {key!r}")
    if not errors:
        row.update(updates)
    return errors


def missing(row: dict) -> list[str]:
    need = [f for f in ("type", "area", "needs_engineer") if not row[f]]
    if row["duplicate_of"] == "?":
        need.append("duplicate_of (closed as duplicate; give the number or d=-)")
    return need


def example(row: dict) -> str:
    parts = []
    if not row["type"]:
        parts.append("t=b")
    if not row["area"]:
        parts.append("a=other")
    if not row["needs_engineer"]:
        parts.append("e=y")
    if row["duplicate_of"] == "?":
        parts.append("d=12345 (or d=-)")
    return " ".join(parts)


def show_suggestion(s: dict, out: Callable[[str], None]) -> None:
    out(f"  suggested by {s['model']} (check it):")
    dup = s["duplicate_of"] if str(s["duplicate_of"]) not in ("0", "") else "-"
    for field, value in (
        ("type", s["type"]),
        ("area", s["area"]),
        ("engineer", s["needs_engineer"]),
        ("duplicate_of", dup),
    ):
        key = "needs_engineer" if field == "engineer" else field
        out(f"    {field}={value}: {s.get(key + '_reason', '')}")


def show(row: dict, issue: dict | None, out: Callable[[str], None]) -> None:
    out("")
    out(f"#{row['number']} {issue['title'] if issue else row['title']}")
    if issue:
        labels = ", ".join(json.loads(issue.get("labels") or "[]")) or "-"
        out(
            f"  {issue.get('html_url')}  [{issue.get('state')}"
            f"{'/' + issue['state_reason'] if issue.get('state_reason') else ''}]  "
            f"labels: {labels}"
        )
        body = re.sub(r"\s+", " ", issue.get("body") or "")[:600]
        out(f"  {body}")
        if issue.get("first_comment"):
            comment = re.sub(r"\s+", " ", issue["first_comment"])[:200]
            out(f"  first comment: {comment}")
    out(
        f"  -> type={row['type'] or '?'} area={row['area'] or '?'} "
        f"engineer={row['needs_engineer'] or '?'} duplicate_of={row['duplicate_of'] or '-'} "
        f"doc_url={row['doc_url'] or '-'}" + (f"  (seed: {row['seed']})" if row["seed"] else "")
    )


def label_loop(
    conn: sqlite3.Connection,
    cfg: Config,
    path: Path,
    ask: Callable[[str], str] | None = None,
    out: Callable[[str], None] = print,
    suggestions_path: Path | None = None,
    only: list[str] | None = None,
    source: str = "hand",
) -> dict:
    """Walk unconfirmed rows (or just the `only` numbers, in that order) for confirmation."""
    from .suggest import changed_fields, read_suggestions

    ask = ask or input  # looked up at call time

    suggestions = read_suggestions(suggestions_path) if suggestions_path else {}
    rows = read_labels(path)
    if only is not None:
        by_number = {r["number"]: r for r in rows}
        todo = [by_number[n] for n in only if n in by_number and by_number[n]["confirmed"] != "yes"]
    else:
        todo = [r for r in rows if r["confirmed"] != "yes"]
    areas = ", ".join(cfg.areas)
    out(f"{len(rows) - len(todo)} of {len(rows)} rows confirmed.")
    out(HELP)
    out(f"  areas: {areas}")
    done = 0
    for row in todo:
        issue = conn.execute(
            "SELECT * FROM issues WHERE repo = ? AND number = ?", (cfg.repo, int(row["number"]))
        ).fetchone()
        show(row, dict(issue) if issue else None, out)
        if row["number"] in suggestions:
            show_suggestion(suggestions[row["number"]], out)
        while True:
            try:
                answer = ask("  > ").strip()
            except (KeyboardInterrupt, EOFError):  # Ctrl-C / Ctrl-D: same as q
                out("")
                answer = "q"
            if answer == "q":
                return {"confirmed": done, "remaining": len(todo) - done}
            if answer == "s":
                break
            if answer in ("h", "?"):
                out(HELP)
                out(f"  areas: {areas}")
                continue
            errors = apply_edits(row, answer, list(cfg.areas)) if answer else []
            gaps = missing(row) if not errors else []
            if errors:
                out("  " + "; ".join(errors))
                continue
            if gaps:
                out(
                    "  still missing: "
                    + ", ".join(gaps)
                    + ". Type them, e.g. "
                    + example(row)
                    + "  (s = skip row, h = help)"
                )
                continue
            row["confirmed"] = "yes"
            # a spot-checked row stays spot-checked when re-confirmed after --reopen,
            # so the random sample keeps meaning what it says
            row["source"] = "spot-checked" if row["source"] == "spot-checked" else source
            if row["number"] in suggestions:
                row["changed"] = ",".join(changed_fields(row, suggestions[row["number"]])) or "none"
            write_labels(path, rows)
            done += 1
            break
    return {"confirmed": done, "remaining": len(todo) - done}


def reopen(path: Path, numbers: list[str]) -> list[str]:
    """Mark rows unconfirmed again so `triage label` shows them; values are kept as they are."""
    rows = read_labels(path)
    found = []
    for row in rows:
        if row["number"] in numbers and row["confirmed"] == "yes":
            row["confirmed"] = ""
            found.append(row["number"])
    write_labels(path, rows)
    return found
