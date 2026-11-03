from triage.docs import Chunk
from triage.prompt import (
    MAX_BODY_CHARS,
    TOOL_NAME,
    build_request,
    build_system_prompt,
    build_tool,
    build_user_message,
)

AREAS = {"auth": "Auth things", "storage": "Storage things", "other": "Else"}
ISSUE = {
    "number": 40012,
    "title": "signInWithPassword fails",
    "body": "It fails.",
    "state": "open",
    "labels": '["bug", "auth"]',
    "first_comment": "Can you share logs?",
}
CHUNK = Chunk(path="auth/passwords.mdx", heading="Signing in", url="https://d/x#a", text="Use it.")


def make_request(issue=ISSUE, chunks=(CHUNK,), areas=AREAS):
    return build_request(
        repo="supabase/supabase",
        model="claude-haiku-5-5",
        effort="low",
        max_tokens=4000,
        areas=areas,
        issue=issue,
        chunks=list(chunks),
    )


def test_system_prompt_lists_areas_and_is_stable():
    a = build_system_prompt("supabase/supabase", AREAS)
    assert "- auth: Auth things" in a and "- storage: Storage things" in a
    assert TOOL_NAME in a and "supabase/supabase" in a
    assert a == build_system_prompt("supabase/supabase", AREAS)  # no timestamps etc.


def test_tool_schema_is_strict_with_area_enum():
    tool = build_tool(AREAS)
    assert tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["area"]["enum"] == ["auth", "storage", "other"]
    assert set(schema["required"]) == set(schema["properties"])


def test_request_caches_fixed_part_and_keeps_issue_after_breakpoint():
    req = make_request()
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "signInWithPassword" not in req["system"][0]["text"]
    assert req["tool_choice"] == {"type": "auto"}
    assert req["output_config"] == {"effort": "low"}
    # the cached prefix is identical for two different issues
    other = make_request(issue={**ISSUE, "number": 1, "title": "x", "body": "y"})
    assert other["system"] == req["system"] and other["tools"] == req["tools"]


def test_user_message_contents():
    text = build_user_message(ISSUE, [CHUNK])
    assert "<title>signInWithPassword fails</title>" in text
    assert "Can you share logs?" in text
    assert 'url="https://d/x#a"' in text
    assert '"bug"' not in text and "labels" not in text  # labels are eval ground truth


def test_user_message_marks_cut_body_and_no_docs():
    long_issue = {**ISSUE, "body": "x" * (MAX_BODY_CHARS + 50), "first_comment": None}
    text = build_user_message(long_issue, [])
    assert "[... cut: 50 more characters not shown]" in text
    assert "(no relevant documentation found)" in text
    assert "<first_comment>" not in text


def test_cached_prefix_is_long_enough_to_cache():
    """Haiku 5.5 only caches prefixes of at least ~512 tokens; ~4 chars per token."""
    import json

    req = make_request()
    prefix = json.dumps(req["tools"]) + req["system"][0]["text"]
    assert len(prefix) / 4 > 600
