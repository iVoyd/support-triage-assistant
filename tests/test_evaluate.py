import csv

from tests.conftest import FakeAnthropic
from tests.test_run import setup_db
from triage.evaluate import (
    agreement,
    classification,
    duplicate_scores,
    evaluate,
    retrieval_scores,
    threshold_sweep,
    write_scorecard,
)
from triage.labels import COLUMNS

U1 = "https://d/auth/passwords#signing-in"
U2 = "https://d/storage/uploads#limits"


def test_classification():
    res = classification(
        [("bug", "bug"), ("bug", "question"), ("feature", "feature")],
        ["bug", "question", "feature", "docs"],
    )
    assert res["accuracy"] == 2 / 3
    assert res["per_class"]["bug"] == {
        "precision": 1.0,
        "recall": 0.5,
        "support": 2,
        "predicted": 1,
    }
    assert res["per_class"]["question"]["precision"] == 0.0
    assert "docs" not in res["per_class"]
    assert res["confusion"][("bug", "question")] == 1


def test_agreement_shows_over_flagging():
    res = agreement([(True, True), (False, True), (False, True), (False, False)])
    assert res["agreement"] == 0.5
    assert res["predicted_true_rate"] == 0.75 and res["label_true_rate"] == 0.25
    assert res["false_positives"] == 2 and res["false_negatives"] == 0


def test_duplicate_scores():
    res = duplicate_scores([({52546}, 52546), (set(), 7), (set(), None), ({5}, None)])
    assert res["precision_at_1"] == 0.5  # 2 flagged, 1 right
    assert res["recall"] == 0.5  # 2 labelled, 1 found
    assert duplicate_scores([(set(), None)])["precision_at_1"] is None


def test_duplicate_groups_are_undirected_and_transitive():
    from triage.evaluate import duplicate_groups, gold_duplicates

    # Theo labelled the older 52550 as a duplicate of the newer 52636
    groups = duplicate_groups([(52550, 52636), (43230, 43142), (43231, 43142)])
    assert gold_duplicates(52636, groups) == {52550}  # the checker can find this
    assert gold_duplicates(52550, groups) == set()  # nothing older to find
    # two duplicates of one issue are duplicates of each other
    assert gold_duplicates(43231, groups) == {43142, 43230}
    assert gold_duplicates(43142, groups) == set()
    assert gold_duplicates(1, groups) == set()


def rec(number, retrieved, scores=None, relevant=None):
    return {"number": number, "retrieved": retrieved, "scores": scores, "relevant": relevant}


def test_retrieval_scores_compare_pages_not_anchors():
    records = [
        rec(1, [U1 + "x", U2], relevant=[U2]),
        rec(2, [U2]),
        rec(3, [], relevant=[]),
        rec(4, [U2], relevant=[]),
    ]
    labels = {1: "https://d/auth/passwords", 2: "https://d/auth/passwords", 3: "none", 4: "none"}
    r = retrieval_scores(records, labels)
    assert (r["labelled_hits"], r["labelled_with_doc"]) == (1, 2)
    assert (r["labelled_none_but_docs_sent"], r["labelled_none"]) == (1, 2)
    assert (r["model_judged_hit"], r["model_judged"]) == (1, 3)


def test_threshold_sweep():
    records = [
        rec(1, [U1, U2], [30.0, 12.0], [U1]),
        rec(2, [U2], [22.0], []),
        rec(3, [U1], None, None),
    ]  # v1 record: no scores, not counted
    rows = {r["min_score"]: r for r in threshold_sweep(records, [0, 20, 40])}
    assert rows[0] == {
        "min_score": 0,
        "relevant_kept": 1,
        "irrelevant_kept": 2,
        "issues_with_a_relevant_doc": 1,
        "issues": 2,
    }
    assert (rows[20]["relevant_kept"], rows[20]["irrelevant_kept"]) == (1, 1)
    assert (rows[40]["relevant_kept"], rows[40]["irrelevant_kept"]) == (0, 0)


def write_labels(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


def test_evaluate_end_to_end_runs_missing_records_and_writes_scorecard(conn, cfg, tmp_path):
    setup_db(conn, cfg)
    labels = tmp_path / "labels.csv"
    write_labels(
        labels,
        [
            {
                "number": "40012",
                "type": "bug",
                "area": "auth",
                "needs_engineer": "no",
                "doc_url": "https://supabase.com/docs/guides/auth/passwords",
                "confirmed": "yes",
            },
            {"number": "40010", "type": "feature", "area": "storage", "confirmed": ""},
            {"number": "99999", "type": "bug", "area": "auth", "confirmed": "yes"},  # not stored
        ],
    )
    client = FakeAnthropic("claude_valid.json")  # one triage call; no duplicate is similar
    logs = []
    report = evaluate(conn, cfg, labels, client, log=logs.append)
    assert len(client.requests) == 1
    assert report["n"] == 1 and report["confirmed_only"]
    assert report["type"]["accuracy"] == 1.0 and report["area"]["accuracy"] == 1.0
    assert report["needs_engineer"]["agreement"] == 0.0  # model said yes, label says no
    assert report["retrieval"]["labelled_hits"] == 1
    assert report["duplicates"]["predicted_duplicates"] == 0
    assert any("not in the database" in m for m in logs)

    out = tmp_path / "SCORECARD.md"
    write_scorecard(report, out)
    text = out.read_text()
    assert "model `claude-haiku-5-5`" in text and "prompt `v3`" in text
    assert "| Type accuracy | 100% | 1 |" in text
    assert "| **bug** | 1 |" in text
    assert "## Retrieval threshold" in text

    # scoring again needs no new calls
    again = evaluate(conn, cfg, labels, FakeAnthropic(), log=lambda m: None)
    assert again["n"] == 1


def test_issues_line_says_how_labels_were_made():
    from triage.evaluate import issues_line, label_counts

    rows = [
        {"confirmed": "yes", "source": ""},
        {"confirmed": "yes", "source": "spot-checked"},
        {"confirmed": "", "source": "suggested"},
        {"confirmed": "", "source": "suggested"},
    ]
    assert issues_line({"n": 4, "label_counts": label_counts(rows)}) == (
        "4 labelled issues: 2 checked by hand (1 fully, 1 spot-checked); "
        "2 accepted from the suggester without a check."
    )
