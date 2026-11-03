import sqlite3

from triage.db import connect


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE triage (id INTEGER PRIMARY KEY, repo TEXT, number INTEGER)")
    old.commit()
    old.close()
    conn = connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(triage)")}
    assert {"retrieved_scores", "relevant_docs"} <= cols
    connect(path).close()  # running again is a no-op
