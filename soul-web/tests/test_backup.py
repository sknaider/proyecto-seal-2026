import sqlite3

import pytest
from soul_web.backup import create_backup


def test_backup_is_consistent_and_non_destructive(tmp_path):
    source = tmp_path / "soul.db"
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE memories(id INTEGER PRIMARY KEY, embedding BLOB, content TEXT)"
        )
        conn.execute(
            "INSERT INTO memories(embedding,content) VALUES(?,?)",
            (b"\x00" * 4096, "Pixel"),
        )
    destination = tmp_path / "backup.db"
    receipt = create_backup(source, destination)
    assert receipt["memory_count"] == 1
    assert receipt["embedding_dimensions"] == [1024]
    assert len(receipt["sha256"]) == 64
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "Pixel"


def test_backup_refuses_overwrite(tmp_path):
    source = tmp_path / "soul.db"
    destination = tmp_path / "backup.db"
    source.write_bytes(b"not sqlite")
    destination.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        create_backup(source, destination)
    assert destination.read_bytes() == b"keep"
