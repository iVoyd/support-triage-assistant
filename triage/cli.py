"""Command-line entry point: triage fetch | docs | run | show."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import load_config
from .db import connect


def cmd_fetch(args, cfg, conn):
    from .github import GitHubClient, RateLimited, fetch_issues

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("note: no GITHUB_TOKEN set; unauthenticated limit is 60 requests/hour.")
    gh = GitHubClient(token=token)
    try:
        result = fetch_issues(gh, conn, cfg.repo, args.limit)
    except RateLimited as e:
        print(f"stopped: {e} Progress so far is saved.")
        return 2
    print(
        f"done: {result['issues']} issues, {result['comments']} first comments fetched, "
        f"{result['requests']} API requests"
    )
    return 0


def cmd_docs(args, cfg, conn):
    from .docs import chunk_folder, fetch_source, store_chunks

    source = args.source or cfg.docs_source
    subdir = cfg.docs_subdir if args.subdir is None else args.subdir
    url_base = (args.url_base or cfg.docs_url_base).rstrip("/")
    root = fetch_source(source, subdir, Path(args.db).parent / "docs-cache")
    chunks = chunk_folder(root, url_base)
    store_chunks(conn, source, chunks)
    files = len({c.path for c in chunks})
    print(f"indexed {len(chunks)} chunks from {files} files in {root}")
    if args.source and args.source != cfg.docs_source:
        print(
            "note: --source differs from triage.toml [docs].source; `triage run` reads chunks "
            "for the configured source. Update triage.toml to use these."
        )
    return 0


def cmd_run(args, cfg, conn):
    import anthropic

    from .run import run_triage

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 1
    if args.model:
        cfg.model = args.model
    client = anthropic.Anthropic()
    totals = run_triage(conn, cfg, client, args.limit, numbers=args.number)
    n = totals["issues"]
    if n:
        print(
            f"done: {n} issues, {totals['valid']} valid, total ${totals['cost_usd']:.4f}, "
            f"avg ${totals['cost_usd'] / n:.5f}/issue, avg {totals['latency_ms'] // n} ms/issue"
        )
    else:
        print("nothing to triage: every stored issue already has a record (or none fetched).")
    return 0


def cmd_show(args, cfg, conn):
    rows = conn.execute(
        """SELECT t.*, i.title, i.html_url FROM triage t
           JOIN issues i ON i.repo = t.repo AND i.number = t.number
           WHERE t.repo = ? ORDER BY t.id DESC LIMIT ?""",
        (cfg.repo, args.limit),
    ).fetchall()
    for r in rows:
        print(f"#{r['number']} {r['title']}\n  {r['html_url']}")
        print(
            f"  type={r['type']} area={r['area']} severity={r['severity']} "
            f"needs_engineer={bool(r['needs_engineer'])} confidence={r['confidence']}"
        )
        print(f"  engineer: {r['needs_engineer_reason']}")
        print(f"  summary: {r['summary']}")
        if args.drafts:
            print("  draft:\n    " + (r["draft_reply"] or "").replace("\n", "\n    "))
            print("  docs: " + ", ".join(json.loads(r["retrieved_urls"] or "[]")))
        if not r["valid"]:
            print(f"  INVALID: {r['errors']}")
        print(
            f"  {r['model']} {r['prompt_version']} in={r['input_tokens']} "
            f"out={r['output_tokens']} cache_read={r['cache_read_tokens']} "
            f"cache_write={r['cache_write_tokens']} ${r['cost_usd'] or 0:.5f} "
            f"{r['latency_ms']}ms\n"
        )
    agg = conn.execute(
        """SELECT COUNT(*) n, SUM(valid) v, SUM(cost_usd) c, AVG(latency_ms) l
           FROM triage WHERE repo = ?""",
        (cfg.repo,),
    ).fetchone()
    if agg["n"]:
        print(
            f"all records for {cfg.repo}: {agg['n']} ({agg['v']} valid), "
            f"total ${agg['c'] or 0:.4f}, avg latency {agg['l']:.0f} ms"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="triage", description="Read-only GitHub issue triage.")
    p.add_argument("--config", default="triage.toml")
    p.add_argument("--db", default="data/triage.db")
    p.add_argument("--repo", help="owner/name; overrides triage.toml")
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="fetch issues + first comments into SQLite")
    f.add_argument("--limit", type=int, default=300)
    f.set_defaults(func=cmd_fetch)

    d = sub.add_parser("docs", help="pull docs, chunk by heading, store for BM25")
    d.add_argument("--source", help="git URL or local path; default from triage.toml")
    d.add_argument("--subdir", help="docs folder inside the source")
    d.add_argument("--url-base", help="public URL of that folder")
    d.set_defaults(func=cmd_docs)

    r = sub.add_parser("run", help="triage untriaged issues with Claude")
    r.add_argument("--limit", type=int, default=20)
    r.add_argument("--number", type=int, action="append", help="triage this issue (repeatable)")
    r.add_argument("--model", help="override the model from triage.toml")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("show", help="print stored triage records")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--drafts", action="store_true", help="include draft replies")
    s.set_defaults(func=cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.repo:
        cfg.repo = args.repo
    conn = connect(args.db)
    try:
        return args.func(args, cfg, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
