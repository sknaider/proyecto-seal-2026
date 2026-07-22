"""Regression tests for task-drive alert scoping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_nerves import _task_alert_candidates


def test_alert_candidates_exclude_undated_gam_and_other_buckets():
    tasks = [
        {"id": "1", "title": "real overdue", "deadline_state": "overdue_3h"},
        {"id": "2", "title": "undated backlog", "deadline_state": "none"},
        {"id": "3", "title": "one hour", "deadline_state": "overdue_1h"},
        {"id": "gam_x", "title": "GAM event"},
    ]

    assert _task_alert_candidates(tasks, "overdue_3h") == [tasks[0]]
    assert _task_alert_candidates(tasks, "overdue_1h") == [tasks[2]]


def test_alert_candidates_preserve_legacy_injected_contexts():
    tasks = [{"id": "legacy", "title": "legacy overdue context"}]

    assert _task_alert_candidates(tasks, "overdue_3h") == tasks
