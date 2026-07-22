from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "audit_soul_skills.py"
SPEC = importlib.util.spec_from_file_location("audit_soul_skills", MODULE_PATH)
audit_soul_skills = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = audit_soul_skills
SPEC.loader.exec_module(audit_soul_skills)


def write_skill(path: Path, name: str, body: str = "# Good\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: Use when testing.\n---\n\n{body}", encoding="utf-8")


def test_valid_skill_passes(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root / "good-skill" / "SKILL.md", "good-skill")
    report = audit_soul_skills.audit([root])
    assert report["counts"]["skills"] == 1
    assert report["counts"]["errors"] == 0
    assert report["counts"]["structural_pass"] == 1


def test_broken_link_and_invalid_name_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root / "Bad_Name" / "SKILL.md", "Bad_Name", "[missing](references/nope.md)\n")
    report = audit_soul_skills.audit([root])
    codes = {item["code"] for item in report["findings"]}
    assert {"name-invalid", "broken-local-link"} <= codes
    assert report["counts"]["structural_fail"] == 1


def test_duplicates_are_visible_but_not_structural_failure(tmp_path: Path) -> None:
    root_a = tmp_path / "skills"
    root_b = tmp_path / "tools" / "skills"
    write_skill(root_a / "same" / "SKILL.md", "same")
    write_skill(root_b / "same" / "SKILL.md", "same")
    report = audit_soul_skills.audit([root_a, root_b])
    assert report["counts"]["duplicate_name_groups"] == 1
    assert report["counts"]["duplicate_content_groups"] == 1
    assert report["counts"]["errors"] == 0
    assert report["counts"]["warnings"] >= 4


def test_destructive_command_requires_gate_language(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root / "unsafe" / "SKILL.md", "unsafe", "Run `rm -rf /tmp/example`.\n")
    report = audit_soul_skills.audit([root])
    assert "destructive-without-gate" in {item["code"] for item in report["findings"]}


def test_atomic_json_writer(tmp_path: Path) -> None:
    target = tmp_path / "out" / "report.json"
    audit_soul_skills.write_json(target, {"ok": True})
    assert target.read_text(encoding="utf-8").endswith("\n")
    assert not target.with_suffix(".json.tmp").exists()


def test_crlf_frontmatter_and_code_like_links_are_not_false_errors(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    path = root / "portable" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(
        b"---\r\nname: portable\r\ndescription: Use when portable.\r\n---\r\n\r\n"
        b"```python\r\nvalue = Thing(success=True, items=[])\r\n```\r\n"
    )
    report = audit_soul_skills.audit([root])
    assert report["counts"]["errors"] == 0


def test_embedded_privileged_dsn_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root / "unsafe-db" / "SKILL.md", "unsafe-db", "Connect to `postgresql://seal:password@localhost/db`.\n")
    report = audit_soul_skills.audit([root])
    assert "embedded-privileged-dsn" in {item["code"] for item in report["findings"]}
    assert report["counts"]["structural_fail"] == 1


def test_retired_reference_is_not_reported_as_live_stale_dependency(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root / "topology" / "SKILL.md", "topology", "Port 8766 is retired and must remain down.\nQdrant was removed.\n")
    report = audit_soul_skills.audit([root])
    assert not ({"legacy-port-8766", "qdrant-retired"} & {item["code"] for item in report["findings"]})
