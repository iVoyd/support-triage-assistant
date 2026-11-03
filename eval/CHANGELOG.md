# Prompt changelog

Each prompt change, why it was made, and its effect on the scorecard. Both versions stay in
`triage/prompt.py`, so `triage eval --prompt v1` and `--prompt v2` can be compared on the
same labels.

## v2 (Milestone 2)

Why: reading the 20-issue v1 run (README, "First run").

- `needs_engineer` defined more narrowly. True only for work on the project's code,
  configuration or infrastructure, or investigating a likely defect, regression or
  security problem. False for usage questions, feature requests, requests about one hosted
  project or account (they go to support), and docs wording fixes. v1 said true for 19/20.
- Drafts must describe only what is true now. No "I've passed this to the team", "we can
  reproduce", "we'll look into it". A validation check rejects these phrases and sends
  the draft back once.
- New field `relevant_docs`: which retrieved docs actually help. Drafts may cite only
  those. This gives a per-issue retrieval signal for the scorecard.
- Not a prompt change, but in the same run: chunks with a BM25 score below
  `min_score` (20) are no longer sent.

Score delta vs v1: pending the first labelled eval run.

| Metric | v1 | v2 |
| --- | --- | --- |
| Type accuracy | – | – |
| Area accuracy | – | – |
| Needs-engineer agreement | – | – |
| Model says engineer / labels say engineer | – | – |

## v1 (Milestone 1)

First version. 20/20 valid on the first real run, $0.00084 per issue.
