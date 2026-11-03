import json

import httpx

from tests.conftest import load_fixture
from triage.github import GitHubClient, upsert_issue
from triage.labels import (
    apply_edits,
    duplicate_target,
    fetch_duplicate_examples,
    label_loop,
    read_labels,
    seed_labels,
    seed_row,
)

REPO = "supabase/supabase"
AREAS = ["auth", "database", "docs", "other"]


def recorded(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    path = request.url.path
    if path == "/search/issues":
        assert "reason:duplicate" in request.url.params["q"]
        return httpx.Response(200, json=load_fixture("github_search_duplicates.json"))
    if path.endswith("/comments"):
        number = path.split("/")[-2]
        return httpx.Response(200, json=load_fixture("github_comments_duplicates.json")[number])
    if path == f"/repos/{REPO}/issues/52546":
        return httpx.Response(200, json=load_fixture("github_issue_52546.json"))
    raise AssertionError(f"unexpected {request.url}")


def gh():
    return GitHubClient(transport=httpx.MockTransport(recorded))


def test_duplicate_target():
    assert duplicate_target(["Closing, duplicate of #52546."], 52547) == "52546"
    assert duplicate_target(["Same as #10"], 11) == "10"
    assert duplicate_target(["see the fix in #7 and #8"], 9) == "?"
    assert duplicate_target(["Tracked in #77"], 9) == "77"  # a lone reference
    assert duplicate_target([""], 9) == "?"


def test_seed_row_maps_labels_and_never_seeds_engineer(cfg):
    issue = {"number": 1, "title": "t", "labels": json.dumps(["bug", "auth", "to-triage"])}
    row = seed_row(issue, cfg)
    assert (row["type"], row["area"], row["needs_engineer"]) == ("bug", "auth", "")
    assert row["seed"] == "github labels: bug, auth"
    assert seed_row({"number": 2, "title": "t", "labels": "[]"}, cfg)["seed"] == ""


def test_fetch_duplicates_stores_issue_and_target(conn, cfg):
    found = fetch_duplicate_examples(gh(), conn, cfg, 10, log=lambda m: None)
    assert found == {52547: "52546"}
    stored = {r[0] for r in conn.execute("SELECT number FROM issues")}
    assert stored == {52546, 52547}


def test_seed_labels_puts_duplicates_and_triaged_first_and_keeps_existing(conn, cfg, tmp_path):
    for n in range(100, 110):
        upsert_issue(
            conn,
            REPO,
            {
                "number": n,
                "title": f"issue {n}",
                "labels": [],
                "created_at": f"2026-09-{n - 99:02d}",
            },
        )
    upsert_issue(
        conn,
        REPO,
        {
            "number": 90,
            "title": "has label",
            "labels": [{"name": "bug"}],
            "created_at": "2026-08-01",
        },
    )
    found = fetch_duplicate_examples(gh(), conn, cfg, 10, log=lambda m: None)
    path = tmp_path / "labels.csv"
    assert seed_labels(conn, cfg, path, 5, found) == (0, 5)
    rows = read_labels(path)
    numbers = [int(r["number"]) for r in rows]
    assert {52547, 52546, 90} <= set(numbers)  # duplicate pair, then a labelled issue
    dup = next(r for r in rows if r["number"] == "52547")
    assert dup["duplicate_of"] == "52546" and dup["type"] == "docs"
    # confirmed rows survive re-seeding untouched
    rows[0]["confirmed"], rows[0]["notes"] = "yes", "checked"
    from triage.labels import write_labels

    write_labels(path, rows)
    assert seed_labels(conn, cfg, path, 7, found) == (5, 2)
    again = {r["number"]: r for r in read_labels(path)}
    assert again[rows[0]["number"]]["notes"] == "checked"


def test_apply_edits():
    row = {
        "type": "",
        "area": "",
        "needs_engineer": "",
        "duplicate_of": "?",
        "doc_url": "",
        "notes": "",
    }
    assert apply_edits(row, 't=b a=au e=y d=#52546 u=none n="two words"', AREAS) == []
    assert row == {
        "type": "bug",
        "area": "auth",
        "needs_engineer": "yes",
        "duplicate_of": "52546",
        "doc_url": "none",
        "notes": "two words",
    }
    assert apply_edits(row, "a=d", AREAS)  # database or docs: ambiguous
    assert apply_edits(row, "t=x e=maybe", AREAS)
    assert row["type"] == "bug"  # unchanged after errors
    assert apply_edits(row, "a=docs d=-", AREAS) == [] and row["duplicate_of"] == ""


def test_label_loop_saves_each_answer(conn, cfg, tmp_path):
    found = fetch_duplicate_examples(gh(), conn, cfg, 10, log=lambda m: None)
    path = tmp_path / "labels.csv"
    seed_labels(conn, cfg, path, 2, found)
    answers = iter(["", "e=n", "q"])  # 52547: missing engineer -> error, then fix
    out = []
    result = label_loop(conn, cfg, path, ask=lambda _: next(answers), out=out.append)
    assert result == {"confirmed": 1, "remaining": 1}
    assert any("still missing: needs_engineer. Type them, e.g. e=y" in line for line in out)
    assert any(line.startswith("  areas: auth, database") for line in out)
    rows = {r["number"]: r for r in read_labels(path)}
    assert rows["52547"]["confirmed"] == "yes" and rows["52547"]["needs_engineer"] == "no"
    assert rows["52546"]["confirmed"] == ""


def test_ctrl_c_quits_cleanly(conn, cfg, tmp_path):
    found = fetch_duplicate_examples(gh(), conn, cfg, 10, log=lambda m: None)
    path = tmp_path / "labels.csv"
    seed_labels(conn, cfg, path, 2, found)

    def interrupt(_):
        raise KeyboardInterrupt

    assert label_loop(conn, cfg, path, ask=interrupt, out=lambda m: None) == {
        "confirmed": 0,
        "remaining": 2,
    }
