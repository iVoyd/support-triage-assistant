# Prompt changelog

Each prompt change, why it was made, and its effect on the scorecard. Both versions stay in
`triage/prompt.py`, so `triage eval --prompt v1` and `--prompt v2` can be compared on the
same labels.

## v4 (proposed)

Why: in Theo's first review of 20 v3 drafts, only 2 went out unchanged (mean edit distance
0.47). His edits fell into five kinds; v4 targets each:

| Review finding | Drafts | v4 change |
| --- | --- | --- |
| Asks for information already in the issue | ~8 | Prompt: re-read body and first comment; ask only for what is missing (not checkable in code) |
| Talks about its own inputs ("the excerpts provided") | 5 | Prompt rule, plus a check that sends such drafts back once |
| Ignores a duplicate the duplicate step confirmed | 4 | The confirmed duplicate goes into the prompt; the draft must name it by number (checked). `triage eval` now runs duplicate checks before triage |
| Cites or promises a link without including it | 3 | Prompt rule, plus a check: "see the link below" with no URL is sent back |
| Claims an action ("I've labelled this") | 1 | Labelled, tagged, assigned, closed and moved join the claimed-action check |

v4 is v3 plus one inserted block of draft rules (tested), and the issue message gains a
`<likely_duplicate>` element when one was confirmed. The new checks apply to v4 only, so
v1–v3 results stay reproducible. v3 stays the default until v4 is measured.

Measure: `triage eval --prompt v4 --out eval/SCORECARD-v4.md` (classification should not
get worse), then `triage review --prompt v4 --like v3`, which re-reviews the same 20 issues.
v4 is better if the approval rate rises and the edit distance falls, without losing type,
area or needs-engineer accuracy.

Score delta vs v3: pending.

## v3

Why: in the v2 scorecard, 14 of 20 type misses were issues labelled `other` that the model
called `bug` or `question`. The label guide and the prompt meant different things by
`other`. Theo's rule, now in both the prompt and the label suggester's guide:

- `other` covers account or billing matters that only support can act on: a restricted or
  paused project, quotas, plans, dashboard or account sign-in. It also covers spam and
  empty templates.
- A technical problem on one project is never `other`. It is `question` if it is about
  setup or usage, and `bug` if the evidence shows a product defect.

Only the `type` definitions changed. The rest of v2 is unchanged (tested). The examples in
the prompt are generic on purpose. Theo's examples were issues from the labelled set
(52587, 52541, 52565), and naming them would grade v3 partly on issues it had been shown.

Labels: some rows labelled `other` before this rule may now be `question` or `bug`. Theo
re-checks them with `triage label --reopen` before the v3 eval.

Score delta vs v2 (`eval/SCORECARD.md` vs `eval/SCORECARD-v2.md`):

| Metric | v2 | v3 |
| --- | --- | --- |
| Type accuracy | 68% | 82% |
| Area accuracy | 79% | 81% |
| Needs-engineer agreement | 90% | 90% |
| Model says engineer / labels say engineer | 48% / 58% | 52% / 58% |
| Avg cost per issue | $0.00083 | $0.00079 |

These deltas are provisional. Between the two runs Theo changed 12 labels: 11 type
re-checks under the new rule, and the area of 52234. So the type gain mixes the prompt
change with the relabelling. Re-scoring the stored v2 records on the current labels
(`triage eval --prompt v2 --no-run --out eval/SCORECARD-v2-relabelled.md`, no API calls)
separates the two.

First human review (v3, 20 drafts): 2 approved as is, 17 edited, 1 rejected, mean edit
distance 0.47.

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
