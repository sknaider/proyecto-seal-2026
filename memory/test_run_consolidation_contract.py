from pathlib import Path
import subprocess


SCRIPT = Path(__file__).with_name("run_consolidation.sh")


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_run_consolidation_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_run_consolidation_uses_maintained_least_privilege_paths():
    source = _source()
    assert "./end_session.sh ADA" in source
    assert "run_unit seal-consolidate.service" in source
    assert "run_unit 'seal-agent-self-reflect@ADA.service'" in source
    assert "run_unit 'seal-agent-self-reflect@JARVIS.service'" in source
    assert "run_unit seal-soul-backup.service" in source
    assert "$PYTHON soul_reflect.py" not in source
    assert "$PYTHON consolidate.py" not in source
    assert "$PYTHON soul_backup.py" not in source


def test_run_consolidation_fails_closed_before_success_claim():
    source = _source()
    assert "set -Eeuo pipefail" in source
    assert "consolidation FAILED" in source
    assert '[[ "$result" != "success" || "$status" != "0" ]]' in source
    success = source.index("consolidation COMPLETE (5/5 actions verified)")
    for required in (
        "./end_session.sh ADA",
        "run_unit seal-consolidate.service",
        "run_unit 'seal-agent-self-reflect@ADA.service'",
        "run_unit 'seal-agent-self-reflect@JARVIS.service'",
        "run_unit seal-soul-backup.service",
    ):
        assert source.index(required) < success
