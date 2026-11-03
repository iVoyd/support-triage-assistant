"""Prompt v4: rules from Theo's first review session, and the checks that enforce them."""

import json

from tests.conftest import FakeAnthropic, load_fixture
from tests.test_duplicates import store
from tests.test_run import setup_db
from triage.duplicates import run_duplicates
from triage.prompt import build_system_prompt, build_user_message
from triage.run import likely_duplicate, run_triage
from triage.validate import validate_output

AREAS = {"auth": "Auth", "cli": "CLI", "other": "Else"}
ALLOWED = ["https://supabase.com/docs/guides/auth/passwords#signing-in-with-an-email-and-password"]


def good():
    return dict(load_fixture("claude_valid.json")["content"][1]["input"])


def check(draft, version="v4", duplicate=None):
    return validate_output(
        dict(good(), draft_reply=draft), list(AREAS), ALLOWED, version, duplicate
    )


def test_v4_prompt_adds_rules_and_keeps_v3():
    v3 = build_system_prompt("r", AREAS, "v3")
    v4 = build_system_prompt("r", AREAS, "v4")
    for rule in (
        "Never ask for\ninformation the reporter already gave",
        "Never mention your own inputs",
        "include its full URL",
        "name it by number",
        "labelled, tagged, assigned, closed or moved",
    ):
        assert rule in v4 and rule not in v3
    # v4 is v3 plus one inserted block, nothing else
    from triage.prompt import V4_DRAFT_RULES

    inserted = (
        "Do not say you labelled, tagged, assigned, closed or moved the issue either.\n"
        + V4_DRAFT_RULES
    )
    assert v4.replace(inserted, "") == v3


def test_duplicate_goes_into_the_message_only_when_given():
    issue = {"number": 52565, "title": "Bug", "body": "PGRST303", "state": "open"}
    dup = {"number": 52541, "title": "PGRST303 after sign-in", "reason": "same JWT rejection"}
    assert '<likely_duplicate number="52541"' in build_user_message(issue, [], dup)
    assert "likely_duplicate" not in build_user_message(issue, [])


def test_v4_checks_catch_each_review_finding():
    # (2) talking about its own inputs
    assert any(
        "talks about your inputs" in e
        for e in check("The documentation provided doesn't cover this.")
    )
    assert any("talks about your inputs" in e for e in check("None of the excerpts help."))
    # (3) ignoring a confirmed duplicate
    assert any("#52541" in e for e in check("Could you share the logs?", duplicate=52541))
    assert check("This looks like the same problem as #52541.", duplicate=52541) == []
    # (4) pointing at a link without giving it
    assert any("no URL" in e for e in check("See the link below for the steps."))
    assert check(f"See the guide: {ALLOWED[0]}") == []
    # (5) claiming an action
    assert any("claims an action" in e for e in check("I've labelled this as a bug."))
    # ordinary replies still pass
    for ok in (
        "Thanks for the information provided. Could you share the error message?",
        "Upgrading to Node 22.23.4 should fix the install.",
    ):
        assert check(ok) == [], ok


def test_v4_checks_do_not_apply_to_older_versions():
    assert check("None of the excerpts help. I've labelled this.", version="v3") == [
        e
        for e in check("None of the excerpts help. I've labelled this.", version="v3")
        if "claims an action" not in e and "inputs" not in e
    ]
    assert not any("inputs" in e for e in check("The excerpts don't help.", version="v3"))


def test_v4_triage_sees_the_confirmed_duplicate(conn, cfg):
    setup_db(conn, cfg)
    store(conn, cfg.repo)
    cfg.prompt_version = "v4"
    run_duplicates(
        conn, cfg, FakeAnthropic("claude_dup_yes.json"), 1, numbers=[52547], log=lambda m: None
    )
    assert likely_duplicate(conn, cfg, 52547)["number"] == 52546
    # the recorded draft does not mention #52546, so v4 sends it back once
    client = FakeAnthropic("claude_valid.json", "claude_valid.json")
    run_triage(conn, cfg, client, 1, numbers=[52547], log=lambda m: None)
    sent = client.requests[0]["messages"][0]["content"]
    assert '<likely_duplicate number="52546"' in sent and "Same title" in sent
    feedback = client.requests[1]["messages"][-1]["content"][0]["content"]
    assert "#52546" in feedback
    row = conn.execute("SELECT valid, errors FROM triage WHERE number = 52547").fetchone()
    assert row["valid"] == 0 and "#52546" in json.loads(row["errors"])[0]
    assert likely_duplicate(conn, cfg, 40012) is None
