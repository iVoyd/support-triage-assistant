"""Duplicate detection with real titles from supabase/supabase (bodies are paraphrased).

52547 was closed as a duplicate of 52546: same title, same report.
52565 and 52541 both report PGRST303. The recorded model reply calls them different; Theo's
label says they are duplicates, so on real data the scorecard would count that as a miss.
These tests check the mechanism, not who is right.
"""

import json

from tests.conftest import FakeAnthropic, load_fixture
from triage.duplicates import (
    TfidfIndex,
    describe,
    run_duplicates,
    top_duplicate,
    validate,
)

ISSUES = load_fixture("dup_issues.json")


def by_number(n):
    return next(i for i in ISSUES if i["number"] == n)


def store(conn, repo):
    for i in ISSUES:
        conn.execute(
            """INSERT INTO issues (repo, number, title, body, state, state_reason, html_url,
                                   created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                repo,
                i["number"],
                i["title"],
                i["body"],
                i["state"],
                i["state_reason"],
                i["html_url"],
                i["created_at"],
            ),
        )
    conn.commit()


def test_tfidf_ranks_the_known_duplicate_first():
    top = TfidfIndex(ISSUES).similar(by_number(52547), 5)
    assert top[0][0]["number"] == 52546
    assert top[0][1] > 0.9
    assert all(s < 0.2 for _, s in top[1:])


def test_tfidf_only_looks_at_older_issues():
    assert [c["number"] for c, _ in TfidfIndex(ISSUES).similar(by_number(52546), 5)][0] != 52547
    assert TfidfIndex(ISSUES).similar(by_number(52484), 5) == []  # the oldest issue


def test_validate_requires_one_entry_per_candidate():
    ok = {"candidates": [{"number": 1, "same_problem": False, "reason": "different"}]}
    assert validate(ok, [1]) == []
    assert validate({"candidates": []}, [1])
    assert validate({"candidates": [{"number": 1, "same_problem": "no", "reason": ""}]}, [1])


def test_run_records_confirmed_and_rejected_candidates(conn, cfg):
    store(conn, cfg.repo)
    client = FakeAnthropic("claude_dup_no.json", "claude_dup_yes.json")  # newest first
    totals = run_duplicates(conn, cfg, client, 20, numbers=[52547, 52565], log=lambda m: None)
    assert totals["issues"] == 2 and totals["model_calls"] == 2 and totals["duplicates"] == 1
    assert top_duplicate(conn, cfg.repo, 52547, cfg.model) == 52546
    assert top_duplicate(conn, cfg.repo, 52565, cfg.model) is None
    rows = describe(conn, cfg.repo, 52565, cfg.model)
    assert rows[0]["candidate"] == 52541 and rows[0]["is_duplicate"] == 0
    assert "PGRST303" in rows[0]["reason"]
    # only candidates above min_similarity went to the model
    sent = client.requests[1]["messages"][0]["content"]
    assert 'number="52546"' in sent and 'number="52484"' not in sent
    assert client.requests[1]["tools"][0]["strict"] is True


def test_no_model_call_when_nothing_is_similar(conn, cfg):
    store(conn, cfg.repo)
    client = FakeAnthropic()  # any call would fail: no recorded replies
    totals = run_duplicates(conn, cfg, client, 20, numbers=[52593], log=lambda m: None)
    assert totals == {"issues": 1, "model_calls": 0, "duplicates": 0, "cost_usd": 0.0}
    count = conn.execute("SELECT COUNT(*) FROM duplicates WHERE number = 52593").fetchone()[0]
    assert count == 1  # recorded as checked


def test_checked_issues_are_skipped_and_bad_answers_retried(conn, cfg):
    store(conn, cfg.repo)
    client = FakeAnthropic("claude_dup_bad.json", "claude_dup_yes.json")
    run_duplicates(conn, cfg, client, 20, numbers=[52547], log=lambda m: None)
    feedback = client.requests[1]["messages"][-1]["content"][0]
    assert feedback["is_error"] and "52546" in feedback["content"]
    # default selection: newest unchecked first, so 52547 is not picked again
    checked = run_duplicates(conn, cfg, FakeAnthropic(), 1, log=lambda m: None)
    assert checked["issues"] == 1
    numbers = [r[0] for r in conn.execute("SELECT DISTINCT number FROM duplicates")]
    assert sorted(numbers) == [52547, 52636]
    assert json.dumps(numbers)  # rows are plain ints
