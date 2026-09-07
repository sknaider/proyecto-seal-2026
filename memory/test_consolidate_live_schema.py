from datetime import datetime, timezone

import pytest

from consolidate import _format_event_line, _source_event_ids, validate_live_schema


def test_format_event_line_uses_created_at_not_removed_time_or_ref_id():
    row = {
        "created_at": datetime(2026, 7, 10, 22, 15, tzinfo=timezone.utc),
        "event_type": "milestone",
        "content": "schema vivo",
        "metadata": {"ref_id": "legacy-inside-json"},
    }
    assert _format_event_line(row) == "[22:15] milestone: schema vivo"


def test_source_event_ids_accepts_jsonb_strings_and_dicts():
    rows = [
        {"metadata": '{"source_event_ids":[1,"2",3]}'},
        {"metadata": {"source_event_ids": [3, 4]}},
        {"metadata": '{"source_event_ids":["invalid",null]}'},
        {"metadata": "not-json"},
    ]
    assert _source_event_ids(rows) == {1, 2, 3, 4}


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, *_args):
        return self.rows


@pytest.mark.asyncio
async def test_validate_live_schema_accepts_current_contract():
    rows = []
    from consolidate import REQUIRED_COLUMNS

    for table, columns in REQUIRED_COLUMNS.items():
        rows.extend({"table_name": table, "column_name": column} for column in columns)
    await validate_live_schema(_FakeConn(rows))


@pytest.mark.asyncio
async def test_validate_live_schema_fails_before_writes_on_drift():
    with pytest.raises(RuntimeError, match="event_log"):
        await validate_live_schema(_FakeConn([]))
