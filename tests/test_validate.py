from tests.conftest import load_fixture
from triage.validate import count_sentences, urls_in, validate_output

AREAS = ["auth", "storage", "other"]
ALLOWED = ["https://supabase.com/docs/guides/auth/passwords#signing-in-with-an-email-and-password"]


def good():
    return dict(load_fixture("claude_valid.json")["content"][1]["input"])


def test_recorded_output_is_valid():
    assert validate_output(good(), AREAS, ALLOWED) == []


def test_count_sentences():
    assert count_sentences("One. Two! Three?") == 3
    assert count_sentences("Uses supabase-js 2.45.1 e.g. on Node. Fails") == 2
    assert count_sentences("") == 0


def test_urls_in_strips_trailing_punctuation():
    assert urls_in("see https://a.com/x#y. and (https://b.com/z)") == [
        "https://a.com/x#y",
        "https://b.com/z",
    ]


def test_rejects_unknown_url_and_long_summary():
    data = dict(good(), draft_reply="See https://example.com/made-up.", summary="A. B. C. D.")
    errors = validate_output(data, AREAS, ALLOWED)
    assert any("not allowed: https://example.com/made-up" in e for e in errors)
    assert any("at most 3 sentences" in e for e in errors)


def test_allows_reply_without_links():
    assert validate_output(dict(good(), draft_reply="Could you share logs?"), AREAS, ALLOWED) == []


def test_rejects_bad_enums_and_confidence():
    data = dict(good(), type="crash", area="billing", severity="urgent", confidence=1.5)
    errors = validate_output(data, AREAS, ALLOWED)
    assert len(errors) == 4
    assert validate_output(dict(good(), confidence=True), AREAS, ALLOWED)
    assert validate_output(dict(good(), needs_engineer="yes"), AREAS, ALLOWED)


def test_missing_fields():
    data = good()
    del data["summary"]
    assert validate_output(data, AREAS, ALLOWED) == ["missing fields: summary"]
