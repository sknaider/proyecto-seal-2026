from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from consolidation_rollback import backup_from_archive_metadata, json_dict


def test_backup_from_archive_metadata_reads_consolidation_backup() -> None:
    metadata = {"consolidation_backup": {"original_memory_id": 123, "rollback_token": "tok"}}

    assert backup_from_archive_metadata(metadata) == {"original_memory_id": 123, "rollback_token": "tok"}


def test_backup_from_archive_metadata_rejects_missing_or_invalid() -> None:
    assert backup_from_archive_metadata(None) is None
    assert backup_from_archive_metadata({}) is None
    assert backup_from_archive_metadata({"consolidation_backup": "bad"}) is None


def test_json_dict_accepts_asyncpg_jsonb_string() -> None:
    assert json_dict('{"consolidation_backup": {"original_memory_id": 1}}') == {
        "consolidation_backup": {"original_memory_id": 1}
    }
    assert json_dict({"a": 1}) == {"a": 1}
    assert json_dict(None) == {}
