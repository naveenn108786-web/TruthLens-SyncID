import sqlite3

from truthlens import db


def test_chain_verifies_and_detects_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "custody.db"))   # own database: this test breaks the chain on purpose
    for i in range(5):
        db.log_event("test.event", f"entry {i}", ref=f"T{i}")
    v = db.verify_chain()
    assert v["ok"], v

    con = sqlite3.connect(db.DB_PATH)
    seq = con.execute("SELECT MAX(seq) - 2 FROM custody_log").fetchone()[0]
    original = con.execute("SELECT detail FROM custody_log WHERE seq=?", (seq,)).fetchone()[0]
    con.execute("UPDATE custody_log SET detail='edited after the fact' WHERE seq=?", (seq,))
    con.commit()
    v = db.verify_chain()
    assert not v["ok"] and v["broken_at"] == seq and "changed" in v["reason"]

    # restore, then delete an entry: the gap must be detected too
    con.execute("UPDATE custody_log SET detail=? WHERE seq=?", (original, seq))
    con.commit()
    assert db.verify_chain()["ok"]
    con.execute("DELETE FROM custody_log WHERE seq=?", (seq,))
    con.commit()
    v = db.verify_chain()
    assert not v["ok"] and v["broken_at"] == seq + 1
    con.close()
