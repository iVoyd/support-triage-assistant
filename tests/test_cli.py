"""The new commands wired end to end through the CLI, without network or API key."""

from pathlib import Path

from triage import cli
from triage.labels import read_labels, write_labels

DOCS = Path(__file__).parent / "fixtures" / "docs"


def test_label_seed_then_eval_no_run(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "t.db")
    labels = tmp_path / "labels.csv"
    # put a few issues in the database through the same code path as `triage fetch`
    from tests.test_run import setup_db
    from triage.config import load_config
    from triage.db import connect

    cfg = load_config("triage.toml")
    conn = connect(db)
    setup_db(conn, cfg)
    conn.close()

    assert (
        cli.main(["--db", db, "label", "--seed", "--n", "3", "--dups", "0", "--file", str(labels)])
        == 0
    )
    rows = read_labels(labels)
    assert len(rows) == 3
    assert {r["type"] for r in rows} >= {"bug"}  # seeded from the 'bug' label

    # nothing confirmed: eval refuses unless asked to use seeds
    out = tmp_path / "SCORECARD.md"
    try:
        cli.main(["--db", db, "eval", "--no-run", "--labels", str(labels), "--out", str(out)])
    except SystemExit as e:
        assert "no confirmed rows" in str(e)
    for r in rows:
        r.update(type=r["type"] or "bug", area=r["area"] or "auth", needs_engineer="yes")
    write_labels(labels, rows)
    # no triage records yet with --no-run, so nothing is scored, but the file is written
    assert (
        cli.main(
            [
                "--db",
                db,
                "eval",
                "--no-run",
                "--unconfirmed",
                "--labels",
                str(labels),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert "0 labelled issues" in out.read_text()


def test_dups_and_eval_need_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    db = str(tmp_path / "t.db")
    assert cli.main(["--db", db, "dups"]) == 1
    assert cli.main(["--db", db, "eval"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
