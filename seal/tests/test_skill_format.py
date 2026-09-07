"""Tests for seal/skill_format.py — portable SKILL.md format parser.

Run:
    python3 -m pytest seal/tests/test_skill_format.py -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from seal.skill_format import (
    Skill,
    SkillCatalog,
    SkillManifest,
    SkillParseError,
    _parse_frontmatter,
    load_skill_file,
    load_skill_text,
    render_skill_md,
    save_skill_file,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_MINIMAL_MD = """\
---
name: summarise
description: Summarise text in one paragraph
---

Condense the following: ${*}
"""

_FULL_MD = """\
---
name: react-guide
description: Guide React development
version: 2.1.0
author: team-seal
triggers:
  - react
  - jsx
  - hooks
tags:
  - frontend
  - javascript
argument-hint: "[component]"
---

# React Guide

You are a senior React engineer. Help with: ${*}
"""

_NO_FRONTMATTER = "Just a plain markdown file without frontmatter.\n"

_MISSING_NAME = """\
---
description: No name here
---

body
"""

_MISSING_DESC = """\
---
name: my-skill
---

body
"""


# ── _parse_frontmatter ────────────────────────────────────────────────────────


class ParseFrontmatterTests(unittest.TestCase):
    def test_minimal_parses_fields(self) -> None:
        fm, body = _parse_frontmatter(_MINIMAL_MD)
        self.assertEqual(fm["name"], "summarise")
        self.assertEqual(fm["description"], "Summarise text in one paragraph")
        self.assertIn("Condense", body)

    def test_full_parses_all_fields(self) -> None:
        fm, body = _parse_frontmatter(_FULL_MD)
        self.assertEqual(fm["name"], "react-guide")
        self.assertEqual(fm["version"], "2.1.0")
        self.assertEqual(fm["author"], "team-seal")
        self.assertEqual(fm["triggers"], ["react", "jsx", "hooks"])
        self.assertEqual(fm["tags"], ["frontend", "javascript"])
        self.assertEqual(fm["argument-hint"], "[component]")
        self.assertIn("React Guide", body)

    def test_no_frontmatter_returns_empty_dict(self) -> None:
        fm, body = _parse_frontmatter(_NO_FRONTMATTER)
        self.assertEqual(fm, {})
        self.assertIn("plain markdown", body)

    def test_body_stripped_of_leading_blank_lines(self) -> None:
        _, body = _parse_frontmatter(_MINIMAL_MD)
        self.assertFalse(body.startswith("\n"))

    def test_unclosed_fence_returns_empty(self) -> None:
        text = "---\nname: x\ndescription: y\n"  # no closing ---
        fm, body = _parse_frontmatter(text)
        self.assertEqual(fm, {})

    def test_boolean_values(self) -> None:
        text = "---\nname: x\ndescription: y\nuser-invocable: false\n---\nbody\n"
        fm, _ = _parse_frontmatter(text)
        self.assertFalse(fm["user-invocable"])

    def test_integer_values(self) -> None:
        text = "---\nname: x\ndescription: y\npriority: 5\n---\nbody\n"
        fm, _ = _parse_frontmatter(text)
        self.assertEqual(fm["priority"], 5)

    def test_quoted_string_unquoted(self) -> None:
        text = '---\nname: "my skill"\ndescription: test\n---\nbody\n'
        fm, _ = _parse_frontmatter(text)
        self.assertEqual(fm["name"], "my skill")

    def test_empty_list(self) -> None:
        text = "---\nname: x\ndescription: y\ntriggers: []\n---\nbody\n"
        fm, _ = _parse_frontmatter(text)
        self.assertEqual(fm["triggers"], [])


# ── SkillManifest ─────────────────────────────────────────────────────────────


class SkillManifestTests(unittest.TestCase):
    def test_from_dict_minimal(self) -> None:
        m = SkillManifest.from_dict({"name": "x", "description": "y"})
        self.assertEqual(m.name, "x")
        self.assertEqual(m.version, "1.0.0")
        self.assertEqual(m.triggers, [])
        self.assertEqual(m.tags, [])

    def test_from_dict_full(self) -> None:
        m = SkillManifest.from_dict({
            "name": "react-guide",
            "description": "Guide React",
            "version": "2.0.0",
            "author": "seal",
            "triggers": ["react", "jsx"],
            "tags": ["frontend"],
            "argument-hint": "[comp]",
            "model": "sonnet",
        })
        self.assertEqual(m.author, "seal")
        self.assertEqual(m.triggers, ["react", "jsx"])
        self.assertEqual(m.argument_hint, "[comp]")
        self.assertEqual(m.model, "sonnet")

    def test_to_dict_roundtrip(self) -> None:
        original = {
            "name": "test",
            "description": "Test skill",
            "version": "1.2.3",
            "author": "nexus",
            "triggers": ["t1", "t2"],
            "tags": ["cat"],
        }
        m = SkillManifest.from_dict(original)
        d = m.to_dict()
        self.assertEqual(d["name"], "test")
        self.assertEqual(d["version"], "1.2.3")
        self.assertEqual(d["triggers"], ["t1", "t2"])

    def test_extra_fields_preserved(self) -> None:
        m = SkillManifest.from_dict({
            "name": "x",
            "description": "y",
            "custom-field": "value",
        })
        self.assertEqual(m.extra.get("custom-field"), "value")
        d = m.to_dict()
        self.assertEqual(d.get("custom-field"), "value")

    def test_user_invocable_defaults_true(self) -> None:
        m = SkillManifest.from_dict({"name": "x", "description": "y"})
        self.assertTrue(m.user_invocable)

    def test_user_invocable_false_preserved(self) -> None:
        m = SkillManifest.from_dict({
            "name": "x", "description": "y", "user-invocable": False
        })
        self.assertFalse(m.user_invocable)


# ── load_skill_text ───────────────────────────────────────────────────────────


class LoadSkillTextTests(unittest.TestCase):
    def test_loads_minimal(self) -> None:
        skill = load_skill_text(_MINIMAL_MD)
        self.assertEqual(skill.name, "summarise")
        self.assertIn("Condense", skill.body)

    def test_loads_full(self) -> None:
        skill = load_skill_text(_FULL_MD)
        self.assertEqual(skill.name, "react-guide")
        self.assertEqual(skill.version, "2.1.0")
        self.assertEqual(skill.triggers, ["react", "jsx", "hooks"])

    def test_raises_on_missing_name(self) -> None:
        with self.assertRaises(SkillParseError) as ctx:
            load_skill_text(_MISSING_NAME)
        self.assertIn("name", str(ctx.exception))

    def test_raises_on_missing_description(self) -> None:
        with self.assertRaises(SkillParseError) as ctx:
            load_skill_text(_MISSING_DESC)
        self.assertIn("description", str(ctx.exception))

    def test_raises_on_no_frontmatter(self) -> None:
        with self.assertRaises(SkillParseError):
            load_skill_text(_NO_FRONTMATTER)

    def test_source_path_attached(self) -> None:
        p = Path("/fake/skills/test.md")
        skill = load_skill_text(_MINIMAL_MD, source=p)
        self.assertEqual(skill.source, p)


# ── Skill.render ─────────────────────────────────────────────────────────────


class SkillRenderTests(unittest.TestCase):
    def _skill(self, body: str) -> Skill:
        return Skill(
            manifest=SkillManifest(name="t", description="t"),
            body=body,
        )

    def test_positional_substitution(self) -> None:
        s = self._skill("Hello ${1}, you are ${2}")
        self.assertEqual(s.render("Alice", "welcome"), "Hello Alice, you are welcome")

    def test_star_substitution(self) -> None:
        s = self._skill("Do: ${*}")
        self.assertEqual(s.render("foo", "bar"), "Do: foo bar")

    def test_named_substitution(self) -> None:
        s = self._skill("Fix ${issue} in ${file}")
        self.assertEqual(s.render(issue="bug-42", file="main.py"),
                         "Fix bug-42 in main.py")

    def test_no_args_leaves_body(self) -> None:
        s = self._skill("Static body")
        self.assertEqual(s.render(), "Static body")

    def test_partial_substitution(self) -> None:
        s = self._skill("${1} and ${2}")
        result = s.render("only-one")
        self.assertIn("only-one", result)
        self.assertIn("${2}", result)


# ── render_skill_md ───────────────────────────────────────────────────────────


class RenderSkillMdTests(unittest.TestCase):
    def test_roundtrip_minimal(self) -> None:
        original = load_skill_text(_MINIMAL_MD)
        md = render_skill_md(original)
        restored = load_skill_text(md)
        self.assertEqual(restored.name, original.name)
        self.assertEqual(restored.description, original.description)

    def test_roundtrip_full(self) -> None:
        original = load_skill_text(_FULL_MD)
        md = render_skill_md(original)
        restored = load_skill_text(md)
        self.assertEqual(restored.triggers, original.triggers)
        self.assertEqual(restored.tags, original.tags)
        self.assertEqual(restored.version, original.version)
        self.assertEqual(restored.manifest.author, original.manifest.author)

    def test_output_starts_with_fence(self) -> None:
        skill = load_skill_text(_MINIMAL_MD)
        md = render_skill_md(skill)
        self.assertTrue(md.startswith("---\n"))

    def test_body_preserved(self) -> None:
        skill = load_skill_text(_MINIMAL_MD)
        md = render_skill_md(skill)
        self.assertIn("Condense", md)

    def test_list_fields_rendered_as_yaml_list(self) -> None:
        skill = load_skill_text(_FULL_MD)
        md = render_skill_md(skill)
        self.assertIn("triggers:", md)
        self.assertIn("  - react", md)


# ── load_skill_file / save_skill_file ─────────────────────────────────────────


class FileIoTests(unittest.TestCase):
    def test_load_and_save_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.md"
            original = load_skill_text(_FULL_MD)
            save_skill_file(original, path)
            restored = load_skill_file(path)
            self.assertEqual(restored.name, original.name)
            self.assertEqual(restored.triggers, original.triggers)

    def test_save_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nested" / "deep" / "skill.md"
            skill = load_skill_text(_MINIMAL_MD)
            save_skill_file(skill, path)
            self.assertTrue(path.exists())

    def test_load_nonexistent_raises(self) -> None:
        with self.assertRaises(SkillParseError):
            load_skill_file(Path("/nonexistent/skill.md"))


# ── SkillCatalog ──────────────────────────────────────────────────────────────


class SkillCatalogTests(unittest.TestCase):
    def _make_catalog(self, skills: list[str]) -> tuple[SkillCatalog, tempfile.TemporaryDirectory]:
        tmp = tempfile.TemporaryDirectory()
        d = Path(tmp.name)
        for md in skills:
            skill = load_skill_text(md)
            (d / f"{skill.name}.md").write_text(md, encoding="utf-8")
        cat = SkillCatalog([d])
        return cat, tmp

    def test_loads_all_skills(self) -> None:
        cat, tmp = self._make_catalog([_MINIMAL_MD, _FULL_MD])
        try:
            self.assertEqual(len(cat), 2)
        finally:
            tmp.cleanup()

    def test_get_by_name(self) -> None:
        cat, tmp = self._make_catalog([_MINIMAL_MD])
        try:
            skill = cat.get("summarise")
            self.assertIsNotNone(skill)
            self.assertEqual(skill.name, "summarise")
        finally:
            tmp.cleanup()

    def test_get_nonexistent_returns_none(self) -> None:
        cat, tmp = self._make_catalog([_MINIMAL_MD])
        try:
            self.assertIsNone(cat.get("ghost"))
        finally:
            tmp.cleanup()

    def test_all_sorted_by_name(self) -> None:
        cat, tmp = self._make_catalog([_FULL_MD, _MINIMAL_MD])
        try:
            names = [s.name for s in cat.all()]
            self.assertEqual(names, sorted(names))
        finally:
            tmp.cleanup()

    def test_search_by_name_fragment(self) -> None:
        cat, tmp = self._make_catalog([_MINIMAL_MD, _FULL_MD])
        try:
            results = cat.search("react")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].name, "react-guide")
        finally:
            tmp.cleanup()

    def test_search_by_trigger(self) -> None:
        cat, tmp = self._make_catalog([_FULL_MD])
        try:
            results = cat.search("jsx")
            self.assertTrue(any(s.name == "react-guide" for s in results))
        finally:
            tmp.cleanup()

    def test_search_no_match_returns_empty(self) -> None:
        cat, tmp = self._make_catalog([_MINIMAL_MD])
        try:
            results = cat.search("zzz-no-match-xyz")
            self.assertEqual(results, [])
        finally:
            tmp.cleanup()

    def test_by_tag(self) -> None:
        cat, tmp = self._make_catalog([_FULL_MD])
        try:
            results = cat.by_tag("frontend")
            self.assertTrue(any(s.name == "react-guide" for s in results))
        finally:
            tmp.cleanup()

    def test_by_trigger(self) -> None:
        cat, tmp = self._make_catalog([_FULL_MD])
        try:
            results = cat.by_trigger("react")
            self.assertTrue(any(s.name == "react-guide" for s in results))
        finally:
            tmp.cleanup()

    def test_later_dir_overrides_earlier(self) -> None:
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            md_v1 = _MINIMAL_MD  # version 1.0.0 (default)
            md_v2 = _MINIMAL_MD.replace(
                "description: Summarise text in one paragraph",
                "description: Summarise text in one paragraph\nversion: 9.9.9",
            )
            (Path(d1) / "summarise.md").write_text(md_v1, encoding="utf-8")
            (Path(d2) / "summarise.md").write_text(md_v2, encoding="utf-8")
            cat = SkillCatalog([Path(d1), Path(d2)])
            skill = cat.get("summarise")
            self.assertEqual(skill.version, "9.9.9")

    def test_add_programmatic(self) -> None:
        cat = SkillCatalog()
        skill = load_skill_text(_MINIMAL_MD)
        cat.add(skill)
        self.assertIn("summarise", cat)
        self.assertEqual(len(cat), 1)

    def test_remove(self) -> None:
        cat = SkillCatalog()
        cat.add(load_skill_text(_MINIMAL_MD))
        removed = cat.remove("summarise")
        self.assertTrue(removed)
        self.assertEqual(len(cat), 0)

    def test_remove_nonexistent_returns_false(self) -> None:
        cat = SkillCatalog()
        self.assertFalse(cat.remove("ghost"))

    def test_invalid_md_files_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "valid.md").write_text(_MINIMAL_MD, encoding="utf-8")
            (Path(d) / "invalid.md").write_text("no frontmatter here\n", encoding="utf-8")
            cat = SkillCatalog([Path(d)])
            self.assertEqual(len(cat), 1)

    def test_missing_dir_silently_ignored(self) -> None:
        cat = SkillCatalog([Path("/nonexistent/skills/")])
        self.assertEqual(len(cat), 0)

    def test_contains_operator(self) -> None:
        cat = SkillCatalog()
        cat.add(load_skill_text(_MINIMAL_MD))
        self.assertIn("summarise", cat)
        self.assertNotIn("ghost", cat)


# ── Skill.from_markdown / to_markdown ─────────────────────────────────────────


class SkillClassMethodTests(unittest.TestCase):
    def test_from_markdown(self) -> None:
        skill = Skill.from_markdown(_FULL_MD)
        self.assertEqual(skill.name, "react-guide")

    def test_to_markdown_roundtrip(self) -> None:
        skill = Skill.from_markdown(_FULL_MD)
        md    = skill.to_markdown()
        back  = Skill.from_markdown(md)
        self.assertEqual(back.name, skill.name)
        self.assertEqual(back.triggers, skill.triggers)


if __name__ == "__main__":
    unittest.main()
