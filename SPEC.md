# SPEC: support-triage-assistant

An AI triage assistant for an open-source project's GitHub issues. For each new issue it
classifies, finds duplicates, retrieves relevant docs, summarizes, drafts a reply with doc
links, and flags what needs an engineer. A labeled evaluation set measures how good it is,
and a human review step approves or fixes drafts. Read-only: it never posts to GitHub.

Default target project: `supabase/supabase` (busy issue tracker, public docs). Make the
target configurable (`--repo owner/name`, docs source in config) so PostHog or Tailscale
also work.

## Architecture (keep it this simple)

```
fetch (GitHub REST) -> store (SQLite) -> retrieve docs (BM25 over doc chunks)
   -> triage (LLM, structured output) -> duplicates (TF-IDF candidates + LLM confirm)
   -> review queue (CLI or tiny local web page) -> eval + scorecard (markdown)
```

## Milestone 1: data + triage

- `triage fetch --repo supabase/supabase --limit 300`: issues and first comments via the
  GitHub REST API (optional `GITHUB_TOKEN`), with labels, state, timestamps, URLs. Store in
  SQLite. Respect rate limits; resumable.
- `triage docs --source <git url or path>`: pull the project's docs (Markdown), chunk by
  heading, index with BM25 (`rank_bm25`). Store chunk URL anchors for citations.
- `triage run --limit 20`: for each untriaged issue call Claude with tool-use structured
  output: `type` (bug | question | feature | docs | other), `area` (from a configurable
  list), `severity` (low | medium | high), `needs_engineer` (bool + reason), `summary`
  (≤ 3 sentences), `draft_reply` (short, cites only retrieved doc URLs), `confidence`.
  Top-k doc chunks go in the prompt; the fixed instructions use prompt caching.
- Record tokens, cost and latency per issue.
- Done when: running on 20 real issues produces stored triage records; tests cover the
  parser, retrieval, prompt assembly, and output validation with fixtures.

## Milestone 2: duplicates + evaluation

- Duplicate candidates: TF-IDF cosine against existing issues (top 5), then an LLM check
  ("same underlying problem?") with a short reason.
- Labeled set: `eval/labels.csv` with ~60 issues. Seed labels automatically from GitHub
  labels where they map (bug/enhancement/area labels; closed-as-duplicate links), then
  `triage label` lets Theo confirm or fix each row quickly in the terminal.
- `triage eval`: accuracy and per-class precision/recall for `type` and `area`,
  duplicate precision@1, needs-engineer agreement, average cost and latency per issue.
  Writes `eval/SCORECARD.md` with a small confusion table and the run's model + date.
- Done when: the scorecard is generated from a real run and committed.

## Milestone 3: human review + polish

- `triage review`: shows issue, summary, retrieved docs, and draft; Theo approves, edits,
  or rejects. Decisions stored; approval rate and edit distance reported in the scorecard.
  A minimal local web page (FastAPI + plain HTML) is optional if time allows.
- Prompt iteration log: `eval/CHANGELOG.md` noting each prompt change and its score delta.
- README: diagram, quickstart, scorecard summary, cost table, limitations (e.g., labels
  are noisy, drafts are never auto-sent), and how it was built.
- Done when: README is complete and a 30-second demo GIF or screenshots exist.

## Out of scope

Posting to GitHub, vector databases, fine-tuning, multi-repo dashboards.
