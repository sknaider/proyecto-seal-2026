"""Tests for SkillInjector — uses real migrated SKILL.md files in tools/skills/."""

import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.skills.skill_injector import Skill, SkillInjector, SkillNotFoundError

SKILLS_DIR = pathlib.Path(__file__).resolve().parent


def test_list_available_returns_skills():
    injector = SkillInjector(SKILLS_DIR)
    names = injector.list_available()
    assert len(names) > 0
    assert isinstance(names[0], str)


def test_list_available_includes_known_skills():
    injector = SkillInjector(SKILLS_DIR)
    names = injector.list_available()
    assert "research-paper-writing" in names or "vllm-inference" in names or len(names) > 5


def test_load_by_name_known_skill():
    injector = SkillInjector(SKILLS_DIR)
    names = injector.list_available()
    if not names:
        print("[SKIP] no skills found — skipping")
        return
    skill = injector.load(names[0])
    assert skill.name == names[0]
    assert len(skill.content) > 0
    assert skill.path.exists()


def test_load_not_found_raises():
    injector = SkillInjector(SKILLS_DIR)
    raised = False
    try:
        injector.load("nonexistent-skill-xyz-99")
    except SkillNotFoundError:
        raised = True
    assert raised


def test_skill_header_returns_first_nonempty_line():
    skill = Skill(
        name="test",
        content="# Research Paper Writing\nThis skill helps write papers.",
        path=pathlib.Path("/fake"),
    )
    assert "Research Paper Writing" in skill.header


def test_skill_inject_append():
    skill = Skill(name="myskill", content="do this thing", path=pathlib.Path("/fake"))
    result = skill.inject_into("You are an agent.")
    assert result.startswith("You are an agent.")
    assert "do this thing" in result
    assert "myskill" in result


def test_skill_inject_prepend():
    skill = Skill(name="myskill", content="do this thing", path=pathlib.Path("/fake"))
    result = skill.inject_into("You are an agent.", position="prepend")
    assert "do this thing" in result
    assert result.index("do this thing") < result.index("You are an agent.")


def test_injector_inject_method():
    injector = SkillInjector(SKILLS_DIR)
    skill = Skill(name="test", content="skill content here", path=pathlib.Path("/fake"))
    result = injector.inject("base prompt", skill)
    assert "base prompt" in result
    assert "skill content here" in result


def test_injector_inject_by_name():
    injector = SkillInjector(SKILLS_DIR)
    names = injector.list_available()
    if not names:
        return
    result = injector.inject_by_name("base prompt", names[0])
    assert "base prompt" in result


def test_load_path_direct():
    injector = SkillInjector(SKILLS_DIR)
    names = injector.list_available()
    if not names:
        return
    skill = injector.load(names[0])
    skill2 = injector.load_path(str(skill.path))
    assert skill2.content == skill.content


def test_load_path_missing_raises():
    injector = SkillInjector(SKILLS_DIR)
    raised = False
    try:
        injector.load_path("/tmp/nonexistent_seal_skill_99.md")
    except SkillNotFoundError:
        raised = True
    assert raised


def test_deep_category_lookup():
    """Skills in category subdirs (mlops/inference/vllm/) are found by name."""
    injector = SkillInjector(SKILLS_DIR)
    names = injector.list_available()
    mlops_names = [n for n in names if "vllm" in n or "llama" in n or "unsloth" in n]
    if not mlops_names:
        return
    skill = injector.load(mlops_names[0])
    assert skill.content


def test_injector_with_custom_dir():
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp)
        skill_dir = p / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# My Custom Skill\nDo something cool.")

        injector = SkillInjector(p)
        skill = injector.load("my-skill")
        assert skill.name == "my-skill"
        assert "Do something cool" in skill.content

        result = injector.inject("You are NEXUS.", skill)
        assert "Do something cool" in result
        assert "You are NEXUS." in result


def main() -> int:
    tests = [
        test_list_available_returns_skills,
        test_list_available_includes_known_skills,
        test_load_by_name_known_skill,
        test_load_not_found_raises,
        test_skill_header_returns_first_nonempty_line,
        test_skill_inject_append,
        test_skill_inject_prepend,
        test_injector_inject_method,
        test_injector_inject_by_name,
        test_load_path_direct,
        test_load_path_missing_raises,
        test_deep_category_lookup,
        test_injector_with_custom_dir,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
