"""AI-suggested labels: blind to the assistant, a different model, stored separately."""

import json

import httpx
import pytest

from tests.conftest import FakeAnthropic, load_fixture
from tests.test_duplicates import ISSUES, store
from triage.evaluate import labels_note
from triage.github import GitHubClient
from triage.labels import label_loop, read_labels, write_labels
from triage.suggest import change_stats, read_suggestions, suggest_labels

MARKER = "TRIAGE_ASSISTANT_OUTPUT_MARKER"


def search(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET" and request.url.path == "/search/issues"
    assert "is:issue" in request.url.params["q"]
    items = [
        {"number": i["number"], "title": i["title"], "body": i["body"], "state": i["state"]}
        for i in ISSUES
    ]
    return httpx.Response(200, json={"items": items})


def setup(conn, cfg, tmp_path, rows):
    store(conn, cfg.repo)
    # the assistant's own output, which the suggester must never see
    conn.execute(
        """INSERT INTO triage (repo, number, model, prompt_version, type, area, summary,
               draft_reply, retrieved_urls, valid, created_at)
           VALUES (?, 52547, 'claude-haiku-5-5', 'v2', 'bug', 'auth', ?, ?, ?, 1, 'now')""",
        (cfg.repo, MARKER, MARKER, json.dumps(["https://docs/" + MARKER])),
    )
    conn.execute(
        """INSERT INTO duplicates (repo, number, candidate, similarity, is_duplicate, reason,
               model, run_id, created_at)
           VALUES (?, 52547, 52484, 0.9, 1, ?, 'claude-haiku-5-5', 'r', 'now')""",
        (cfg.repo, MARKER),
    )
    conn.commit()
    labels = tmp_path / "labels.csv"
    base = {"type": "", "area": "", "needs_engineer": "", "duplicate_of": "", "confirmed": ""}
    write_labels(labels, [{**base, **r} for r in rows])
    return labels, tmp_path / "suggestions.csv"


def gh():
    return GitHubClient(token="t", transport=httpx.MockTransport(search))


def test_suggestions_are_blind_stored_separately_and_prefill_rows(conn, cfg, tmp_path):
    labels, sugg = setup(
        conn,
        cfg,
        tmp_path,
        [
            {"number": "52565"},
            {"number": "52547", "duplicate_of": "52546", "seed": "closed as duplicate"},
            {"number": "52541", "type": "bug", "confirmed": "yes"},  # confirmed: left alone
        ],
    )
    client = FakeAnthropic("claude_suggest_52565.json", "claude_suggest_52547.json")
    totals = suggest_labels(conn, cfg, client, gh(), labels, sugg, log=lambda m: None)
    assert totals["suggested"] == 2 and totals["failed"] == 0
    assert totals["cost_usd"] == pytest.approx(2 * (2400 * 2.0 + 600 * 10.0) / 1e6)

    for req in client.requests:
        assert req["model"] == "claude-sonnet-5-5"
        assert MARKER not in json.dumps(req, default=str)  # blind to the assistant
        assert req["tools"][0]["strict"] is True and req["tool_choice"] == {"type": "auto"}
    sent_52547 = client.requests[1]["messages"][0]["content"]
    assert "closed as a duplicate of #52546" in sent_52547
    assert 'candidate number="52546"' in sent_52547
    assert 'candidate number="52547"' not in sent_52547  # only older issues

    stored = read_suggestions(sugg)
    assert set(stored) == {"52565", "52547"}
    assert stored["52547"]["model"] == "claude-sonnet-5-5"
    assert "Same title" in stored["52547"]["duplicate_of_reason"]
    rows = {r["number"]: r for r in read_labels(labels)}
    assert (rows["52547"]["type"], rows["52547"]["area"]) == ("docs", "cli")
    assert rows["52547"]["needs_engineer"] == "yes" and rows["52547"]["duplicate_of"] == "52546"
    assert rows["52565"]["duplicate_of"] == "" and rows["52565"]["confirmed"] == ""
    assert rows["52541"]["area"] == ""  # confirmed row untouched

    # running again does not re-suggest (no API calls left in the fake)
    again = suggest_labels(conn, cfg, FakeAnthropic(), gh(), labels, sugg, log=lambda m: None)
    assert again["suggested"] == 0


def test_bad_duplicate_number_is_sent_back(conn, cfg, tmp_path):
    labels, sugg = setup(conn, cfg, tmp_path, [{"number": "52547", "duplicate_of": "52546"}])
    client = FakeAnthropic("claude_suggest_bad.json", "claude_suggest_52547.json")
    suggest_labels(conn, cfg, client, gh(), labels, sugg, log=lambda m: None)
    feedback = client.requests[1]["messages"][-1]["content"][0]
    assert feedback["is_error"] and "99999" in feedback["content"]


def test_refuses_to_suggest_with_the_evaluated_model(conn, cfg, tmp_path):
    labels, sugg = setup(conn, cfg, tmp_path, [{"number": "52547"}])
    cfg.suggest_model = cfg.model
    with pytest.raises(SystemExit, match="must differ"):
        suggest_labels(conn, cfg, FakeAnthropic(), gh(), labels, sugg, log=lambda m: None)


def test_search_failure_still_suggests(conn, cfg, tmp_path):
    labels, sugg = setup(conn, cfg, tmp_path, [{"number": "52565"}])
    broken = GitHubClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    logs = []
    client = FakeAnthropic("claude_suggest_52565.json")
    assert (
        suggest_labels(conn, cfg, client, broken, labels, sugg, log=logs.append)["suggested"] == 1
    )
    assert any("search failed" in m for m in logs)
    assert "(none found)" in client.requests[0]["messages"][0]["content"]


def test_confirming_records_changes_and_stats(conn, cfg, tmp_path):
    labels, sugg = setup(
        conn,
        cfg,
        tmp_path,
        [
            {"number": "52565"},
            {"number": "52547", "duplicate_of": "52546"},
        ],
    )
    client = FakeAnthropic("claude_suggest_52565.json", "claude_suggest_52547.json")
    suggest_labels(conn, cfg, client, gh(), labels, sugg, log=lambda m: None)
    out = []
    answers = iter(["a=database", ""])  # 52565: change area; 52547: accept as suggested
    label_loop(
        conn, cfg, labels, ask=lambda _: next(answers), out=out.append, suggestions_path=sugg
    )
    assert any("suggested by claude-sonnet-5-5" in line for line in out)
    assert any("duplicate_of=52546: Same title" in line for line in out)
    rows = {r["number"]: r for r in read_labels(labels)}
    assert rows["52565"]["changed"] == "area" and rows["52547"]["changed"] == "none"
    st = change_stats(labels, sugg)
    assert (st["confirmed"], st["suggested"], st["changed"]) == (2, 2, 1)
    assert st["per_field"] == {"type": 0, "area": 1, "needs_engineer": 0, "duplicate_of": 0}
    assert labels_note(st) == (
        "Labels: suggested by a larger model (claude-sonnet-5-5), then reviewed and corrected "
        "by hand; 1 of 2 changed (area 1)."
    )


def test_fixture_matches_tool_schema():
    from triage.suggest import build_tool

    schema = build_tool(["auth", "cli", "other"])["input_schema"]
    sample = load_fixture("claude_suggest_52547.json")["content"][1]["input"]
    assert set(sample) == set(schema["required"]) == set(schema["properties"])
