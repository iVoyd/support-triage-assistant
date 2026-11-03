# Prompt changelog

Each prompt change, why it was made, and its effect on the scorecard. Both versions stay in
`triage/prompt.py`, so `triage eval --prompt v1` and `--prompt v2` can be compared on the
same labels.

## v4 (current default)

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
v1–v3 results stay reproducible.

Measure: `triage eval --prompt v4` (classification should not get worse; the result is now
`eval/SCORECARD.md`), then `triage review --prompt v4 --like v3`, which re-reviews the same 20 issues.
v4 is better if the approval rate rises and the edit distance falls, without losing type,
area or needs-engineer accuracy.

Result (2026-11-03, same 62 labels; `eval/SCORECARD.md` vs `eval/SCORECARD-v3.md`):

| Metric | v3 | v4 |
| --- | --- | --- |
| Type accuracy | 82% | 84% |
| Area accuracy | 81% | 81% |
| Needs-engineer agreement | 90% | 90% |
| Valid outputs | 61 / 62 | 57 / 62 |
| Avg cost / latency per issue | $0.00079 / 5.5 s | $0.00086 / 5.8 s |

Re-review of the same 20 issues (`triage review --like v3`). Two issues had no valid v4
draft, so they count as failures:

| Same 20 issues | v3 | v4 |
| --- | --- | --- |
| Approved as is | 2 (10%) | 5 (25%) |
| Approved after edits | 17 | 13 |
| Rejected | 1 | 0 |
| No valid draft (summary over 3 sentences, also after the retry) | 0 | 2 (52582, 52520) |
| Usable | 19 (95%) | 18 (90%) |
| Mean edit distance, edited drafts | 0.47 | 0.55 |
| Mean edit distance, all usable drafts | 0.42 | 0.39 |

What v4 fixed, by Theo's count:
- Every draft with a confirmed duplicate now names it (0 of 4 under v3).
- No claimed actions.
- Talk about its own inputs is down from 5 drafts to 2.

What got worse:
- Longer summaries. Valid outputs fell from 61 to 57 of 62. All five invalid v4 records
  failed the existing 3-sentence summary rule, also after the retry: 52582 (5 sentences),
  52520 (5), 52267 (4), 52133 (4) and 52222 (6). 52222 also failed the new duplicate check:
  it did not name 52219. So 4 of the 5 failures were the summary limit alone, and none came
  from the self-talk or claimed-action checks. (An earlier version of this entry guessed
  the opposite for 52582 and 52520; the stored errors showed it was wrong.) 52582 and 52520
  are in the re-review set and lost their draft there, because review only offers valid
  drafts. The same rule caused invalid records before (52267 and 52133 under v2), but v4
  tripped it more often.
- The drafts that still needed edits needed bigger ones (0.55 vs 0.47).

Still open:
- Asking for information already in the issue, 5 drafts. This is the v5 target.

Kept as default: classification held (type +2 points, the rest unchanged) and three times
as many drafts went out unchanged. The cost is longer summaries: 5 of 62 records invalid,
including 2 of the 20 re-review issues.

v5 targets:
- Asking for information already in the issue (above).
- The summary limit:
  - Restate "summary: at most 3 sentences" near the end of the v4 block, where the new
    rules pushed it away from the end of the prompt.
  - Make the retry message quote the limit and the summary itself, and ask for it cut to 3
    sentences.
  - Check that the sentence counter is not miscounting these 5 summaries before blaming
    the model.
- Offer records that still fail a check to review, with the errors shown, instead of
  dropping them.

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

Score delta vs v2 (`eval/SCORECARD-v3.md` vs `eval/SCORECARD-v2.md`):

| Metric | v2 | v3 |
| --- | --- | --- |
| Type accuracy | 68% | 82% |
| Area accuracy | 79% | 81% |
| Needs-engineer agreement | 90% | 90% |
| Model says engineer / labels say engineer | 48% / 58% | 52% / 58% |
| Avg cost per issue | $0.00083 | $0.00079 |

Like for like: re-scoring the stored v2 records on the current labels
(`eval/SCORECARD-v2-relabelled.md`, no API calls) gives type 68%, area 81%, needs-engineer
90%. So on the same labels v3 is +14 points on type and level on the rest. The type gain
comes from the prompt change, not the relabelling.

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

Score delta vs v1, 62 labels, 2026-11-03 (`eval/SCORECARD-v1.md` vs `eval/SCORECARD-v2.md`):

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
