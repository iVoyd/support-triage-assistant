from tests.conftest import FakeAnthropic
from triage.config import Prices
from triage.llm import Usage, triage_issue

AREAS = ["auth", "storage", "other"]
ALLOWED = ["https://supabase.com/docs/guides/auth/passwords#signing-in-with-an-email-and-password"]
REQUEST = {
    "model": "claude-haiku-5-5",
    "max_tokens": 10,
    "messages": [{"role": "user", "content": "x"}],
}


def test_valid_first_try():
    client = FakeAnthropic("claude_valid.json")
    result = triage_issue(client, REQUEST, AREAS, ALLOWED)
    assert result.valid and result.output["area"] == "auth"
    assert result.usage.api_calls == 1
    assert result.usage.cache_write_tokens == 1450
    assert result.latency_ms >= 0


def test_invalid_output_is_retried_with_errors_as_tool_result():
    client = FakeAnthropic("claude_bad_url.json", "claude_valid.json")
    result = triage_issue(client, REQUEST, AREAS, ALLOWED)
    assert result.valid
    assert result.usage.api_calls == 2
    retry = client.requests[1]["messages"]
    assert retry[0] == REQUEST["messages"][0]  # original turn unchanged (cache prefix)
    assert retry[1]["role"] == "assistant"
    feedback = retry[2]["content"][0]
    assert feedback["type"] == "tool_result" and feedback["is_error"] is True
    assert feedback["tool_use_id"] == "toolu_02"
    assert "example.com" in feedback["content"]
    assert REQUEST["messages"] == [{"role": "user", "content": "x"}]  # caller's list untouched


def test_gives_up_after_two_bad_answers():
    client = FakeAnthropic("claude_bad_url.json", "claude_bad_url.json")
    result = triage_issue(client, REQUEST, AREAS, ALLOWED)
    assert not result.valid and result.output is not None
    assert any("not allowed" in e for e in result.errors)


def test_no_tool_call_then_reminder():
    client = FakeAnthropic("claude_no_tool.json", "claude_valid.json")
    result = triage_issue(client, REQUEST, AREAS, ALLOWED)
    assert result.valid
    assert "record_triage" in client.requests[1]["messages"][-1]["content"]


def test_claimed_action_is_sent_back_for_a_fix():
    client = FakeAnthropic("claude_claims_action.json", "claude_valid.json")
    result = triage_issue(client, REQUEST, AREAS, ALLOWED)
    assert result.valid and result.usage.api_calls == 2
    assert "claims an action" in client.requests[1]["messages"][-1]["content"][0]["content"]


def test_refusal_stops():
    client = FakeAnthropic("claude_refusal.json")
    result = triage_issue(client, REQUEST, AREAS, ALLOWED)
    assert not result.valid and "refusal" in result.errors[0]
    assert result.usage.api_calls == 1


def test_cost():
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert usage.cost(Prices(input=1, output=2, cache_write=3, cache_read=4)) == 10
    assert usage.cost(None) is None
