"""triage review: approve / edit / reject drafts; stats for the scorecard."""

import csv

import pytest

from tests.conftest import FakeAnthropic
from tests.test_run import setup_db
from triage import cli
from triage.evaluate import write_scorecard
from triage.review import (
    export_reviews,
    pick_unreviewed,
    review_loop,
    review_stats,
    word_edit_distance,
)
from triage.run import run_triage


def triaged(conn, cfg, n=3):
    """n valid triage records: one real run on 40012, copied onto the other fixture issues."""
    setup_db(conn, cfg)
    run_triage(
        conn, cfg, FakeAnthropic("claude_valid.json"), 1, numbers=[40012], log=lambda m: None
    )
    for number in [40010, 40009][: n - 1]:
        conn.execute(
            """INSERT INTO triage (repo, number, model, prompt_version, type, area, severity,
                   needs_engineer, needs_engineer_reason, summary, draft_reply, confidence,
                   retrieved_urls, retrieved_scores, relevant_docs, valid, errors, created_at)
               SELECT repo, ?, model, prompt_version, type, area, severity, needs_engineer,
                   needs_engineer_reason, summary, draft_reply, confidence, retrieved_urls,
                   retrieved_scores, relevant_docs, valid, errors, created_at
               FROM triage WHERE number = 40012""",
            (number,),
        )
    conn.commit()


def test_word_edit_distance():
    assert word_edit_distance("a b c", "a b c") == 0.0
    assert word_edit_distance("a b c d", "a b x d") == 0.25
    assert word_edit_distance("a b", "c d e f") == 1.0
    assert word_edit_distance("", "") == 0.0


def test_review_loop_records_each_decision(conn, cfg):
    triaged(conn, cfg)
    answers = iter(["?", "a", "e", "dropped the second sentence", "r", "wrong docs cited"])
    out = []
    counts = review_loop(
        conn,
        cfg,
        10,
        ask=lambda _: next(answers),
        edit=lambda t: t.split(".")[0] + ".",
        out=out.append,
    )
    assert counts == {"approve": 1, "edit": 1, "reject": 1, "skipped": 0}
    assert any("docs retrieved (* = model judged relevant)" in line for line in out)
    assert any(line.startswith("    * https://supabase.com/docs/guides/auth") for line in out)
    rows = conn.execute(
        "SELECT decision, edit_distance, reason FROM reviews ORDER BY id"
    ).fetchall()
    assert [r["decision"] for r in rows] == ["approve", "edit", "reject"]
    assert rows[0]["edit_distance"] == 0.0
    assert 0 < rows[1]["edit_distance"] < 1 and rows[1]["reason"] == "dropped the second sentence"
    assert rows[2]["edit_distance"] is None and rows[2]["reason"] == "wrong docs cited"
    assert pick_unreviewed(conn, cfg, 10) == []  # everything reviewed

    st = review_stats(conn, cfg.repo, cfg.model, cfg.prompt_version)
    assert (st["n"], st["approved"], st["edited"], st["rejected"]) == (3, 1, 1, 1)
    assert st["approval_rate"] == pytest.approx(1 / 3)
    assert st["usable_rate"] == pytest.approx(2 / 3)
    assert st["mean_edit_distance_usable"] == pytest.approx(rows[1]["edit_distance"] / 2)


def test_unchanged_edit_counts_as_approval_and_quit_keeps_rest(conn, cfg):
    triaged(conn, cfg, 2)
    answers = iter(["e", "q"])
    counts = review_loop(
        conn, cfg, 10, ask=lambda _: next(answers), edit=lambda t: t, out=lambda m: None
    )
    assert counts["approve"] == 1
    assert len(pick_unreviewed(conn, cfg, 10)) == 1


def test_invalid_records_are_not_offered(conn, cfg):
    setup_db(conn, cfg)
    run_triage(
        conn,
        cfg,
        FakeAnthropic("claude_bad_url.json", "claude_bad_url.json"),
        1,
        log=lambda m: None,
    )
    assert pick_unreviewed(conn, cfg, 10) == []


def test_scorecard_and_export(conn, cfg, tmp_path):
    triaged(conn, cfg, 2)
    answers = iter(["a", "r", ""])
    review_loop(conn, cfg, 10, ask=lambda _: next(answers), out=lambda m: None)
    report = {
        "date": "d",
        "repo": cfg.repo,
        "model": cfg.model,
        "prompt_version": "v3",
        "effort": "low",
        "min_score": 0,
        "top_k": 5,
        "n": 0,
        "confirmed_only": True,
        "type": {"n": 0, "accuracy": 0, "per_class": {}, "confusion": {}},
        "area": {
            "n": 0,
            "accuracy": 0,
            "per_class": {},
            "confusion": __import__("collections").Counter(),
        },
        "needs_engineer": {"n": 0, "agreement": 0, "predicted_true_rate": 0, "label_true_rate": 0},
        "duplicates": {
            "precision_at_1": None,
            "predicted_duplicates": 0,
            "recall": None,
            "labelled_duplicates": 0,
        },
        "retrieval": {
            "labelled_hits": 0,
            "labelled_with_doc": 0,
            "model_judged_hit": 0,
            "model_judged": 0,
            "labelled_none_but_docs_sent": 0,
            "labelled_none": 0,
        },
        "sweep": [],
        "valid": 0,
        "avg_cost": 0,
        "avg_latency_ms": 0,
        "dup_checks": 0,
        "dup_cost": 0,
        "review": review_stats(conn, cfg.repo, cfg.model, cfg.prompt_version),
    }
    out = tmp_path / "SCORECARD.md"
    write_scorecard(report, out)
    text = out.read_text()
    assert "## Human review (`triage review`)" in text
    assert "| Approved as is | 1 (50%) |" in text and "| Rejected | 1 |" in text
    path = tmp_path / "reviews.csv"
    assert export_reviews(conn, cfg.repo, path) == 2
    rows = list(csv.DictReader(open(path)))
    assert [r["decision"] for r in rows] == ["approve", "reject"]


def test_cli_review_stats(tmp_path, capsys, cfg):
    from triage.db import connect

    db = tmp_path / "t.db"
    conn = connect(db)
    triaged(conn, cfg, 1)
    review_loop(conn, cfg, 10, ask=lambda _: "a", out=lambda m: None)
    conn.close()
    assert cli.main(["--db", str(db), "review", "--stats"]) == 0
    assert "1 drafts, 1 approved as is (100%)" in capsys.readouterr().out


def test_like_reviews_the_same_issues_under_a_new_prompt(conn, cfg, tmp_path, capsys):
    from triage.review import reviewed_numbers

    cfg.prompt_version = "v3"
    triaged(conn, cfg, 2)
    review_loop(conn, cfg, 1, ask=lambda _: "a", out=lambda m: None)  # reviews 40012 under v3
    assert reviewed_numbers(conn, cfg.repo, "v3") == [40012]
    # a v4 record for the same issue (copied from the v3 one)
    conn.execute(
        """INSERT INTO triage (repo, number, model, prompt_version, type, area, severity,
               needs_engineer, needs_engineer_reason, summary, draft_reply, confidence,
               retrieved_urls, valid, errors, created_at)
           SELECT repo, number, model, 'v4', type, area, severity, needs_engineer,
               needs_engineer_reason, summary, draft_reply, confidence, retrieved_urls, valid,
               errors, created_at FROM triage WHERE number = 40012"""
    )
    conn.commit()
    cfg.prompt_version = "v4"
    offered = pick_unreviewed(conn, cfg, 10, reviewed_numbers(conn, cfg.repo, "v3"))
    assert [r["number"] for r in offered] == [40012]
