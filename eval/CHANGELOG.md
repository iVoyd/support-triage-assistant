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

Score delta vs v1, 62 labels, 2026-11-03 (`eval/SCORECARD-v1.md` vs `eval/SCORECARD.md`):

| Metric | v1 | v2 | Delta |
| --- | --- | --- | --- |
| Type accuracy | 68% | 68% | 0 |
| Area accuracy | 84% | 79% | −5 points (3 issues) |
| Needs-engineer agreement | 81% | 90% | +9 points |
| Model says engineer / labels say engineer | 77% / 58% | 48% / 58% | over-flagging gone, now slightly under |
| Valid outputs | 61 / 62 | 60 / 62 | −1 |
| Avg cost per issue | $0.00077 | $0.00083 | +8% (longer prompt, `relevant_docs`) |

The needs-engineer change worked. The area drop is 3 issues on 62 and within noise, but
worth watching. Type accuracy is held down by a definition mismatch: the labels put
account-specific support requests under `other`, both prompts do not (see README,
"Results"). The v1 records from the first 20-issue run had no `min_score`; the 42 added for
this eval did.

## v1 (Milestone 1)

First version. 20/20 valid on the first real run, $0.00084 per issue.
