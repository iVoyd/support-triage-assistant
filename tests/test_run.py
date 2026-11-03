"""End to end with recorded fixtures: fetch -> docs -> run -> stored records -> show."""

import json
from pathlib import Path

import httpx

from tests.conftest import FakeAnthropic
from tests.test_github import recorded_github
from triage import cli
from triage.docs import chunk_folder, store_chunks
from triage.github import GitHubClient, fetch_issues
from triage.run import pick_issues, run_triage

DOCS = Path(__file__).parent / "fixtures" / "docs"


def setup_db(conn, cfg):
    cfg.min_score = 0.0  # the fixture corpus is tiny, so BM25 scores are small
    gh = GitHubClient(transport=httpx.MockTransport(recorded_github))
    fetch_issues(gh, conn, cfg.repo, 300, log=lambda m: None)
    store_chunks(conn, cfg.docs_source, chunk_folder(DOCS, cfg.docs_url_base))


def test_run_stores_records_with_cost_and_retrieval(conn, cfg):
    setup_db(conn, cfg)
    client = FakeAnthropic("claude_valid.json")
    totals = run_triage(conn, cfg, client, limit=1, log=lambda m: None)
    assert totals["issues"] == 1 and totals["valid"] == 1

    row = conn.execute("SELECT * FROM triage").fetchone()
    assert row["number"] == 40012  # newest untriaged issue first
    assert row["model"] == "claude-haiku-5-5" and row["prompt_version"] == "v2"
    assert (row["type"], row["area"], row["severity"]) == ("bug", "auth", "medium")
    assert row["needs_engineer"] == 1
    urls = json.loads(row["retrieved_urls"])
    scores = json.loads(row["retrieved_scores"])
    assert len(scores) == len(urls) and scores == sorted(scores, reverse=True)
    assert json.loads(row["relevant_docs"]) == [urls[0]]
    assert urls[0].endswith("/auth/passwords#signing-in-with-an-email-and-password")
    # 900 in, 350 out, 1450 cache write at the configured Haiku 5.5 prices
    expected = (900 * 0.10 + 350 * 0.50 + 1450 * 0.125) / 1_000_000
    assert abs(row["cost_usd"] - expected) < 1e-12
    assert row["api_calls"] == 1

    # retrieved docs and the issue made it into the request
    sent = client.requests[0]["messages"][0]["content"]
    assert urls[0] in sent and "Invalid login credentials" in sent
    assert client.requests[0]["model"] == "claude-haiku-5-5"


def test_already_triaged_issues_are_skipped(conn, cfg):
    setup_db(conn, cfg)
    run_triage(conn, cfg, FakeAnthropic("claude_valid.json"), limit=1, log=lambda m: None)
    remaining = [i["number"] for i in pick_issues(conn, cfg.repo, 20)]
    assert remaining == [40010, 40009]
    assert [i["number"] for i in pick_issues(conn, cfg.repo, 20, numbers=[40012])] == [40012]


def test_new_prompt_version_retriages(conn, cfg):
    setup_db(conn, cfg)
    run_triage(conn, cfg, FakeAnthropic("claude_valid.json"), limit=1, log=lambda m: None)
    assert 40012 not in [
        i["number"] for i in pick_issues(conn, cfg.repo, 20, None, cfg.model, "v2")
    ]
    assert 40012 in [i["number"] for i in pick_issues(conn, cfg.repo, 20, None, cfg.model, "v3")]


def test_min_score_drops_weak_docs(conn, cfg):
    setup_db(conn, cfg)
    cfg.min_score = 1000.0
    client = FakeAnthropic("claude_bad_url.json", "claude_bad_url.json")
    run_triage(conn, cfg, client, limit=1, log=lambda m: None)
    assert json.loads(conn.execute("SELECT retrieved_urls FROM triage").fetchone()[0]) == []
    assert "(no relevant documentation found)" in client.requests[0]["messages"][0]["content"]


def test_invalid_result_is_still_stored(conn, cfg):
    setup_db(conn, cfg)
    client = FakeAnthropic("claude_bad_url.json", "claude_bad_url.json")
    run_triage(conn, cfg, client, limit=1, log=lambda m: None)
    row = conn.execute("SELECT valid, errors, input_tokens, api_calls FROM triage").fetchone()
    assert row["valid"] == 0 and "not allowed" in row["errors"]
    assert row["input_tokens"] == 1800 and row["api_calls"] == 2


def test_cli_show_and_docs(tmp_path, capsys, cfg):
    db = tmp_path / "t.db"
    assert cli.main(["--db", str(db), "docs", "--source", str(DOCS), "--subdir", ""]) == 0
    assert "indexed" in capsys.readouterr().out
    assert cli.main(["--db", str(db), "show"]) == 0


def test_cli_run_requires_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["--db", str(tmp_path / "t.db"), "run"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_show_prints_relevant_docs_and_duplicates(tmp_path, capsys, cfg):
    from tests.test_duplicates import store
    from triage.db import connect
    from triage.duplicates import run_duplicates

    db = tmp_path / "t.db"
    conn = connect(db)
    setup_db(conn, cfg)
    store(conn, cfg.repo)
    run_triage(
        conn, cfg, FakeAnthropic("claude_valid.json"), 1, numbers=[52547], log=lambda m: None
    )
    run_duplicates(
        conn, cfg, FakeAnthropic("claude_dup_yes.json"), 1, numbers=[52547], log=lambda m: None
    )
    conn.close()
    assert cli.main(["--db", str(db), "show", "--drafts"]) == 0
    out = capsys.readouterr().out
    assert "judged relevant: https://supabase.com/docs/guides/auth/passwords" in out
    assert "duplicates: #52546 sim=0.9" in out and "SAME: Same title" in out
