from pathlib import Path

from triage.docs import (
    Retriever,
    chunk_folder,
    chunk_markdown,
    fetch_source,
    load_chunks,
    page_url,
    slugify,
    store_chunks,
)

DOCS = Path(__file__).parent / "fixtures" / "docs"
BASE = "https://supabase.com/docs/guides"


def test_slugify_matches_github_style():
    assert slugify("With email") == "with-email"
    assert slugify("Signing in with `signInWithPassword`!") == "signing-in-with-signinwithpassword"


def test_page_url():
    assert page_url("auth/passwords.mdx", BASE) == f"{BASE}/auth/passwords"
    assert page_url("auth.mdx", BASE) == f"{BASE}/auth"
    assert page_url("storage/index.md", BASE) == f"{BASE}/storage"


def test_chunks_by_heading_with_anchors():
    text = (DOCS / "auth" / "passwords.mdx").read_text()
    chunks = chunk_markdown(text, "auth/passwords.mdx", BASE)
    by_heading = {c.heading: c for c in chunks}
    # page intro takes the frontmatter title and the page URL without anchor
    assert by_heading["Password-based Auth"].url == f"{BASE}/auth/passwords"
    signin = by_heading["Signing in with an email and password"]
    assert signin.url == f"{BASE}/auth/passwords#signing-in-with-an-email-and-password"
    # a '## ' line inside a code fence is not a heading
    assert "this is not a heading" in signin.text
    assert "With email" not in by_heading  # heading with no body is skipped
    assert "Resetting a password" in by_heading


def test_custom_anchor_and_mdx_imports_removed():
    chunks = chunk_markdown((DOCS / "auth.mdx").read_text(), "auth.mdx", BASE)
    assert [c.url for c in chunks] == [f"{BASE}/auth", f"{BASE}/auth#auth-providers"]
    assert chunks[1].heading == "Providers"
    assert "import" not in chunks[0].text


def test_long_sections_are_split_with_same_url():
    text = "## Big\n\n" + "\n\n".join(["word " * 100] * 10)
    chunks = chunk_markdown(text, "big.md", BASE)
    assert len(chunks) > 1
    assert {c.url for c in chunks} == {f"{BASE}/big#big"}
    assert all(len(c.text) <= 2000 for c in chunks)


def test_store_and_load_roundtrip(conn):
    chunks = chunk_folder(DOCS, BASE)
    store_chunks(conn, "src", chunks)
    store_chunks(conn, "src", chunks)  # re-indexing replaces, not duplicates
    assert load_chunks(conn, "src") == chunks


def test_local_source_path():
    assert fetch_source(str(DOCS), "auth", Path("/unused")) == DOCS / "auth"


def test_bm25_finds_relevant_section():
    retriever = Retriever(chunk_folder(DOCS, BASE))
    hits = retriever.search("signInWithPassword Invalid login credentials after confirm email", 3)
    assert hits[0][0].url.endswith("#signing-in-with-an-email-and-password")
    top = retriever.search("bucket file size limit per folder", 1)[0][0]
    assert top.url == f"{BASE}/storage/uploads#file-size-limits"


def test_bm25_empty_cases():
    assert Retriever([]).search("anything") == []
    assert Retriever(chunk_folder(DOCS, BASE)).search("the and of") == []
    assert Retriever(chunk_folder(DOCS, BASE)).search("zzzunknownzzz") == []
