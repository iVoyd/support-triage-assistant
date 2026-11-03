import json

import httpx
import pytest

from tests.conftest import load_fixture
from triage.github import GitHubClient, RateLimited, fetch_issues

REPO = "supabase/supabase"


def recorded_github(request: httpx.Request) -> httpx.Response:
    """Serve recorded GitHub responses; fail loudly on anything unexpected."""
    assert request.method == "GET", "the tool must be read-only"
    path = request.url.path
    if path == f"/repos/{REPO}/issues":
        page = request.url.params.get("page")
        return httpx.Response(200, json=load_fixture(f"github_issues_page{page}.json"))
    if path.startswith(f"/repos/{REPO}/issues/") and path.endswith("/comments"):
        number = path.split("/")[-2]
        return httpx.Response(200, json=load_fixture("github_comments.json").get(number, []))
    raise AssertionError(f"unexpected request {request.url}")


def make_client(handler, **kw):
    return GitHubClient(token="t", transport=httpx.MockTransport(handler), **kw)


def test_fetch_stores_issues_skips_prs_and_gets_first_comments(conn):
    result = fetch_issues(make_client(recorded_github), conn, REPO, limit=300, log=lambda m: None)
    assert result["issues"] == 3
    assert result["comments"] == 2
    rows = {r["number"]: r for r in conn.execute("SELECT * FROM issues")}
    assert set(rows) == {40012, 40010, 40009}  # 40011 is a pull request
    bug = rows[40012]
    assert json.loads(bug["labels"]) == ["bug", "auth"]
    assert bug["html_url"] == "https://github.com/supabase/supabase/issues/40012"
    assert bug["first_comment"].startswith("Can you check")
    assert bug["first_comment_author"] == "maintainer1"
    assert rows[40009]["state"] == "closed" and rows[40009]["state_reason"] == "completed"
    assert rows[40010]["first_comment_fetched"] == 0  # no comments, nothing to fetch


def test_fetch_respects_limit(conn):
    result = fetch_issues(make_client(recorded_github), conn, REPO, limit=1, log=lambda m: None)
    assert result["issues"] == 1
    assert conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 1


def test_fetch_is_resumable_and_does_not_refetch_comments(conn):
    fetch_issues(make_client(recorded_github), conn, REPO, limit=300, log=lambda m: None)
    calls = []

    def counting(request):
        calls.append(request.url.path)
        return recorded_github(request)

    result = fetch_issues(make_client(counting), conn, REPO, limit=300, log=lambda m: None)
    assert result["comments"] == 0
    assert not any(p.endswith("/comments") for p in calls)


def test_rate_limit_waits_then_retries():
    sleeps, state = [], {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}
            return httpx.Response(403, headers=headers, json={"message": "rate limit"})
        return httpx.Response(200, json=[])

    gh = make_client(handler, sleep=sleeps.append, now=lambda: 1000.0)
    assert gh.get("/x") == []
    assert sleeps == [31.0]


def test_rate_limit_too_long_raises():
    def handler(request):
        return httpx.Response(429, headers={"retry-after": "3600"})

    with pytest.raises(RateLimited):
        make_client(handler, sleep=lambda s: None).get("/x")


def test_other_errors_raise():
    def handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(httpx.HTTPStatusError):
        make_client(handler).get("/x")
