"""Consistent, non-destructive SQLite backup with a machine-readable receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_backup(source: str | Path, destination: str | Path) -> dict[str, object]:
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(source_path, timeout=30)
    target_db = sqlite3.connect(destination_path, timeout=30)
    try:
        source_db.backup(target_db)
        target_db.commit()
        quick_check = target_db.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"backup quick_check failed: {quick_check}")
        tables = {
            row[0]
            for row in target_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "memories" not in tables:
            raise RuntimeError("backup does not contain the canonical memories table")
        memory_count, min_id, max_id, null_embeddings = target_db.execute(
            "SELECT count(*),min(id),max(id),sum(embedding IS NULL) FROM memories"
        ).fetchone()
        dimensions = sorted(
            {
                int(size) // 4
                for (size,) in target_db.execute(
                    "SELECT DISTINCT length(embedding) FROM memories WHERE embedding IS NOT NULL"
                )
            }
        )
    except BaseException:
        target_db.close()
        source_db.close()
        if destination_path.exists():
            destination_path.unlink()
        raise
    else:
        target_db.close()
        source_db.close()
    return {
        "ok": True,
        "source": str(source_path),
        "destination": str(destination_path),
        "sha256": _sha256(destination_path),
        "quick_check": "ok",
        "memory_count": int(memory_count),
        "min_id": int(min_id or 0),
        "max_id": int(max_id or 0),
        "null_embeddings": int(null_embeddings or 0),
        "embedding_dimensions": dimensions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="soul-web-backup")
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    print(json.dumps(create_backup(args.source, args.destination), ensure_ascii=False))


if __name__ == "__main__":
    main()
