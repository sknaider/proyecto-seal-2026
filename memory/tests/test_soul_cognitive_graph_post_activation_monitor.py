from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import soul_cognitive_graph_post_activation_monitor as monitor


def row(i: int, *, rank_ms: float = 2.0) -> dict:
    return {
        "ts": "2026-06-03T00:00:00+00:00",
        "agent": "ADA",
        "query_hash": f"hash{i}",
        "query_token_count": 3,
        "rank_ms": rank_ms,
        "candidate_count": 5,
        "base_top_ids": [str(i), str(i + 100)],
        "shadow_top_ids": [str(i + 100), str(i)],
    }


def test_build_report_culminates_with_guarded_assist(monkeypatch, tmp_path):
    path = tmp_path / "shadow.jsonl"
    path.write_text(
        "\n".join(__import__("json").dumps(row(i)) for i in range(300)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        monitor,
        "systemd_environment",
        lambda: {
            "ok": True,
            "assist": True,
            "preserve_top1": True,
            "min_tokens_3": True,
            "shadow": True,
            "active_state": "active",
        },
    )
    monkeypatch.setattr(monitor, "seal_core_health", lambda skip=False: {"ok": True, "skipped": skip})

    report = monitor.build_report(telemetry_path=path, min_rows=300, min_query_tokens=3, skip_health=True)

    assert report["culminated"] is True
    assert report["decision"] == "CULMINATED"
    assert report["assist_preserve_top1"]["agreement"]["top1_rate"] == 1.0
    assert report["raw_shadow_reference"]["agreement"]["top1_rate"] == 0.0


def test_build_report_blocks_when_systemd_not_assist(monkeypatch, tmp_path):
    path = tmp_path / "shadow.jsonl"
    path.write_text(
        "\n".join(__import__("json").dumps(row(i)) for i in range(300)),
        encoding="utf-8",
    )
    monkeypatch.setattr(monitor, "systemd_environment", lambda: {"ok": False, "assist": False})
    monkeypatch.setattr(monitor, "seal_core_health", lambda skip=False: {"ok": True, "skipped": skip})

    report = monitor.build_report(telemetry_path=path, min_rows=300, min_query_tokens=3, skip_health=True)

    assert report["culminated"] is False
    assert report["decision"] == "KEEP_MONITORING"
    assert report["assist_preserve_top1"]["decision"]["ready_for_assist"] is True
