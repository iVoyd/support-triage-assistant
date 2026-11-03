"""`triage eval`: score the latest records against eval/labels.csv, write eval/SCORECARD.md."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .duplicates import run_duplicates, top_duplicate
from .labels import read_labels
from .prompt import TYPES
from .run import run_triage

THRESHOLDS = [0, 10, 15, 20, 25, 30, 35, 40, 50]


# ---------- metric helpers ----------


def classification(pairs: list[tuple[str, str]], classes: list[str]) -> dict:
    """pairs are (label, prediction). Returns accuracy, per-class P/R/support, confusion."""
    n = len(pairs)
    correct = sum(1 for gold, pred in pairs if gold == pred)
    per_class = {}
    for c in classes:
        tp = sum(1 for g, p in pairs if g == c and p == c)
        predicted = sum(1 for _, p in pairs if p == c)
        support = sum(1 for g, _ in pairs if g == c)
        if predicted or support:
            per_class[c] = {
                "precision": tp / predicted if predicted else None,
                "recall": tp / support if support else None,
                "support": support,
                "predicted": predicted,
            }
    return {
        "n": n,
        "accuracy": correct / n if n else 0.0,
        "per_class": per_class,
        "confusion": Counter(pairs),
    }


def agreement(pairs: list[tuple[bool, bool]]) -> dict:
    n = len(pairs)
    return {
        "n": n,
        "agreement": sum(1 for g, p in pairs if g == p) / n if n else 0.0,
        "label_true_rate": sum(1 for g, _ in pairs if g) / n if n else 0.0,
        "predicted_true_rate": sum(1 for _, p in pairs if p) / n if n else 0.0,
        "false_positives": sum(1 for g, p in pairs if p and not g),
        "false_negatives": sum(1 for g, p in pairs if g and not p),
    }


def duplicate_scores(pairs: list[tuple[int | None, int | None]]) -> dict:
    """pairs are (labelled duplicate_of, predicted top duplicate), None meaning "none".

    precision@1: of the issues where the system named a duplicate, how often its top pick
    is the labelled one. recall: of the labelled duplicates, how many were found.
    """
    predicted = [(g, p) for g, p in pairs if p is not None]
    positives = [(g, p) for g, p in pairs if g is not None]
    return {
        "n": len(pairs),
        "labelled_duplicates": len(positives),
        "predicted_duplicates": len(predicted),
        "precision_at_1": (
            sum(1 for g, p in predicted if g == p) / len(predicted) if predicted else None
        ),
        "recall": sum(1 for g, p in positives if g == p) / len(positives) if positives else None,
    }


def page(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def retrieval_scores(records: list[dict], doc_labels: dict[int, str]) -> dict:
    """records: latest triage records. doc_labels: {number: doc_url or "none"}."""
    n = len(records)
    with_docs = sum(1 for r in records if r["retrieved"])
    with_relevant = [r for r in records if r["relevant"] is not None]
    judged_hit = sum(1 for r in with_relevant if r["relevant"])
    hits = misses = junk_passed = empty_ok = 0
    for r in records:
        gold = doc_labels.get(r["number"])
        if not gold:
            continue
        if gold == "none":
            if r["retrieved"]:
                junk_passed += 1
            else:
                empty_ok += 1
        elif page(gold) in {page(u) for u in r["retrieved"]}:
            hits += 1
        else:
            misses += 1
    return {
        "n": n,
        "with_docs": with_docs,
        "model_judged": len(with_relevant),
        "model_judged_hit": judged_hit,
        "labelled_with_doc": hits + misses,
        "labelled_hits": hits,
        "labelled_none": junk_passed + empty_ok,
        "labelled_none_but_docs_sent": junk_passed,
        "docs_sent": sum(len(r["retrieved"]) for r in records),
        "docs_relevant": sum(len(r["relevant"] or []) for r in records),
    }


def threshold_sweep(records: list[dict], thresholds: list[float] = THRESHOLDS) -> list[dict]:
    """For each cut-off: how many model-judged relevant / irrelevant docs would still be sent.

    Only docs that were retrieved in the run can be counted, so cut-offs below the run's
    own min_score show the same numbers as min_score itself.
    """
    rows = []
    judged = [r for r in records if r["relevant"] is not None and r["scores"] is not None]
    for t in thresholds:
        kept_rel = kept_irr = issues_with_rel = 0
        for r in judged:
            relevant = {page_anchor(u) for u in r["relevant"]}
            kept = [u for u, s in zip(r["retrieved"], r["scores"], strict=True) if s >= t]
            rel = [u for u in kept if page_anchor(u) in relevant]
            kept_rel += len(rel)
            kept_irr += len(kept) - len(rel)
            issues_with_rel += int(bool(rel))
        rows.append(
            {
                "min_score": t,
                "relevant_kept": kept_rel,
                "irrelevant_kept": kept_irr,
                "issues_with_a_relevant_doc": issues_with_rel,
                "issues": len(judged),
            }
        )
    return rows


def page_anchor(url: str) -> str:
    return url.strip().rstrip("/")


# ---------- gathering ----------


def latest_records(conn: sqlite3.Connection, cfg: Config, numbers: list[int]) -> dict[int, dict]:
    out = {}
    for n in numbers:
        row = conn.execute(
            """SELECT * FROM triage WHERE repo = ? AND number = ? AND model = ?
               AND prompt_version = ? ORDER BY id DESC LIMIT 1""",
            (cfg.repo, n, cfg.model, cfg.prompt_version),
        ).fetchone()
        if row:
            r = dict(row)
            r["retrieved"] = json.loads(r["retrieved_urls"] or "[]")
            r["scores"] = json.loads(r["retrieved_scores"]) if r["retrieved_scores"] else None
            r["relevant"] = json.loads(r["relevant_docs"]) if r["relevant_docs"] else None
            out[n] = r
    return out


def evaluate(
    conn: sqlite3.Connection,
    cfg: Config,
    labels_path: Path,
    client=None,
    unconfirmed: bool = False,
    log=print,
) -> dict:
    labels = read_labels(labels_path)
    # usable: checked by a person, or accepted from the suggester (`--accept-suggestions`)
    rows = [
        r for r in labels if unconfirmed or r["confirmed"] == "yes" or r["source"] == "suggested"
    ]
    if not rows:
        raise SystemExit(
            f"no {'' if unconfirmed else 'confirmed or accepted '}rows in {labels_path}; run "
            "`triage label` or `triage label --accept-suggestions`"
            + ("" if unconfirmed else ", or pass --unconfirmed")
        )
    stored = {r[0] for r in conn.execute("SELECT number FROM issues WHERE repo = ?", (cfg.repo,))}
    missing_issues = [int(r["number"]) for r in rows if int(r["number"]) not in stored]
    if missing_issues:
        log(f"warning: {len(missing_issues)} labelled issues are not in the database; skipped")
    rows = [r for r in rows if int(r["number"]) in stored]
    numbers = [int(r["number"]) for r in rows]

    if client is not None:
        have = latest_records(conn, cfg, numbers)
        todo = [n for n in numbers if n not in have]
        if todo:
            log(f"triaging {len(todo)} labelled issues with {cfg.model} {cfg.prompt_version}")
            run_triage(conn, cfg, client, len(todo), numbers=todo, log=log)
        checked = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT number FROM duplicates WHERE repo = ? AND model = ?",
                (cfg.repo, cfg.model),
            )
        }
        todo = [n for n in numbers if n not in checked]
        if todo:
            log(f"checking {len(todo)} labelled issues for duplicates")
            run_duplicates(conn, cfg, client, len(todo), numbers=todo, log=log)

    records = latest_records(conn, cfg, numbers)
    scored = [r for r in rows if int(r["number"]) in records]
    if len(scored) < len(rows):
        log(f"warning: {len(rows) - len(scored)} labelled issues have no record; skipped")

    def pred(r):
        return records[int(r["number"])]

    type_pairs = [(r["type"], pred(r)["type"]) for r in scored if r["type"]]
    area_pairs = [(r["area"], pred(r)["area"]) for r in scored if r["area"]]
    eng_pairs = [
        (r["needs_engineer"] == "yes", bool(pred(r)["needs_engineer"]))
        for r in scored
        if r["needs_engineer"] in ("yes", "no")
    ]
    dup_pairs = []
    for r in scored:
        if r["duplicate_of"] == "?":
            continue
        gold = int(r["duplicate_of"]) if r["duplicate_of"] else None
        dup_pairs.append((gold, top_duplicate(conn, cfg.repo, int(r["number"]), cfg.model)))
    recs = [records[int(r["number"])] for r in scored]
    doc_labels = {int(r["number"]): r["doc_url"] for r in scored if r["doc_url"]}
    marks = ",".join("?" * len(numbers))
    dup_cost = conn.execute(
        f"""SELECT COUNT(DISTINCT run_id), SUM(cost_usd) FROM duplicates
            WHERE repo = ? AND model = ? AND number IN ({marks})""",
        (cfg.repo, cfg.model, *numbers),
    ).fetchone()
    from .suggest import label_provenance

    return {
        "labels": label_provenance(labels_path, labels_path.with_name("suggestions.csv")),
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "repo": cfg.repo,
        "model": cfg.model,
        "prompt_version": cfg.prompt_version,
        "effort": cfg.effort,
        "min_score": cfg.min_score,
        "top_k": cfg.top_k,
        "n": len(scored),
        "confirmed_only": not unconfirmed,
        "type": classification(type_pairs, TYPES),
        "area": classification(area_pairs, list(cfg.areas)),
        "needs_engineer": agreement(eng_pairs),
        "duplicates": duplicate_scores(dup_pairs),
        "retrieval": retrieval_scores(recs, doc_labels),
        "sweep": threshold_sweep(recs),
        "valid": sum(r["valid"] for r in recs),
        "avg_cost": sum(r["cost_usd"] or 0 for r in recs) / len(recs) if recs else 0.0,
        "avg_latency_ms": sum(r["latency_ms"] or 0 for r in recs) / len(recs) if recs else 0.0,
        "dup_checks": dup_cost[0] or 0,
        "dup_cost": dup_cost[1] or 0.0,
    }


# ---------- markdown ----------


def pct(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def labels_note(st: dict | None) -> str:
    """Where the labels came from, e.g. for the README and the scorecard header."""
    if st and st.get("accepted"):
        sc = st["spot_check"]
        model = ", ".join(st["models"])
        if not sc["checked"]:
            return (
                f"Labels: suggested by {model}; none hand-checked yet, so the label error "
                "rate is unknown."
            )
        lo, hi = sc["interval"]
        text = (
            f"Labels: suggested by {model}; a random {sc['checked']} of {st['accepted']} "
            f"hand-checked, {sc['corrected']} corrected (estimated label error rate "
            f"{sc['rate']:.0%})."
        )
        text += f" 95% interval for that rate: {lo:.0%}–{hi:.0%}."
        if st["hand"]:
            text += f" Another {st['hand']} labelled fully by hand."
        return text
    if not st or not st["suggested"]:
        return "Labels: entered by hand (some seeded from GitHub labels), all reviewed by Theo."
    fields = ", ".join(f"{f} {n}" for f, n in st["per_field"].items() if n)
    return (
        f"Labels: suggested by a larger model ({', '.join(st['models'])}), then reviewed and "
        f"corrected by hand; {st['changed']} of {st['suggested']} changed"
        + (f" ({fields})." if fields else ".")
    )


def write_scorecard(report: dict, path: Path) -> None:
    t, a, e, d, rt = (
        report[k] for k in ("type", "area", "needs_engineer", "duplicates", "retrieval")
    )
    lines = [
        "# Scorecard",
        "",
        f"Generated by `triage eval` on {report['date']} for `{report['repo']}`: "
        f"model `{report['model']}` (effort `{report['effort']}`), prompt "
        f"`{report['prompt_version']}`, top_k {report['top_k']}, "
        f"min_score {report['min_score']:g}.",
        f"{report['n']} labelled issues"
        + (
            " (confirmed by Theo)."
            if report["confirmed_only"]
            else " (including unconfirmed seed labels, so treat these numbers as rough)."
        ),
        labels_note(report.get("labels")),
        "",
        "| Metric | Value | n |",
        "| --- | --- | --- |",
        f"| Type accuracy | {pct(t['accuracy'])} | {t['n']} |",
        f"| Area accuracy | {pct(a['accuracy'])} | {a['n']} |",
        f"| Needs-engineer agreement | {pct(e['agreement'])} | {e['n']} |",
        f"| Needs-engineer: model says yes / labels say yes | {pct(e['predicted_true_rate'])} / "
        f"{pct(e['label_true_rate'])} | {e['n']} |",
        f"| Duplicate precision@1 | {pct(d['precision_at_1'])} | "
        f"{d['predicted_duplicates']} flagged |",
        f"| Duplicate recall | {pct(d['recall'])} | {d['labelled_duplicates']} labelled |",
        f"| Retrieval hit rate (labelled doc page in top_k) | "
        f"{pct(rt['labelled_hits'] / rt['labelled_with_doc'] if rt['labelled_with_doc'] else None)}"
        f" | {rt['labelled_with_doc']} |",
        f"| Retrieval: model found a relevant doc | "
        f"{pct(rt['model_judged_hit'] / rt['model_judged'] if rt['model_judged'] else None)} | "
        f"{rt['model_judged']} |",
        f"| Retrieval: docs sent where label says none fits | "
        f"{rt['labelled_none_but_docs_sent']} of {rt['labelled_none']} issues | |",
        f"| Valid outputs | {report['valid']} / {report['n']} | |",
        f"| Avg triage cost / latency per issue | ${report['avg_cost']:.5f} / "
        f"{report['avg_latency_ms'] / 1000:.1f} s | {report['n']} |",
        f"| Duplicate checks: cost | ${report['dup_cost']:.4f} total | "
        f"{report['dup_checks']} checks |",
        "",
        "## Type: confusion (rows = label, columns = model)",
        "",
        "| label \\ model | " + " | ".join(TYPES) + " |",
        "| --- |" + " --- |" * len(TYPES),
    ]
    for gold in TYPES:
        cells = [str(t["confusion"].get((gold, p), 0) or "·") for p in TYPES]
        lines.append(f"| **{gold}** | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Per class",
        "",
        "| Field | Class | Precision | Recall | Support |",
        "| --- | --- | --- | --- | --- |",
    ]
    for field, res in (("type", t), ("area", a)):
        for c, m in res["per_class"].items():
            lines.append(
                f"| {field} | {c} | {pct(m['precision'])} | {pct(m['recall'])} | {m['support']} |"
            )
    confusions = [(g, p, n) for (g, p), n in a["confusion"].most_common() if g != p][:8]
    if confusions:
        lines += [
            "",
            "## Most common area mix-ups",
            "",
            "| Label | Model | Count |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {g} | {p} | {n} |" for g, p, n in confusions]
    lines += [
        "",
        "## Retrieval threshold",
        "",
        "Docs the model marked relevant vs not, if `min_score` were set to each value. Lower",
        "values than the run's own `min_score` cannot show more docs than were retrieved.",
        "",
        "| min_score | relevant docs kept | irrelevant docs kept | issues with a relevant doc |",
        "| --- | --- | --- | --- |",
    ]
    for s in report["sweep"]:
        lines.append(
            f"| {s['min_score']:g} | {s['relevant_kept']} | {s['irrelevant_kept']} | "
            f"{s['issues_with_a_relevant_doc']} of {s['issues']} |"
        )
    lines += [
        "",
        "Labels are partly seeded from GitHub labels and checked by hand; they are still",
        "one person's judgement. Small classes give noisy per-class numbers.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
