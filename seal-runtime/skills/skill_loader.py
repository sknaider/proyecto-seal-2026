#!/usr/bin/env python3
"""
skill_loader.py — SEAL Skill System
=====================================
Clean-room reimplementation of Claude Code's skills (SPEC_04 + SPEC_09).
Skills are .md files with YAML frontmatter that define reusable prompts/commands.

Anthropic's skills:
  - File-based (.md in ~/.claude/skills/ or .claude/skills/)
  - Frontmatter: name, description, when_to_use, allowed-tools, model, context
  - Shell commands inline, argument substitution
  - 18 bundled skills

SEAL skills:
  - Same file-based approach (.md with frontmatter)
  - Directories: ~/.seal/skills/ (user), .seal/skills/ (project), seal-runtime/skills/bundled/
  - SOUL integration: skills can access memory and emotional state
  - Agent-specific skills (ADA-only, JARVIS-only)

Usage:
    loader = SkillLoader()
    skills = loader.discover()
    skill = loader.get("commit")
    prompt = skill.build_prompt(args="fix: typo in readme")

Standalone:
    python3 skill_loader.py list
    python3 skill_loader.py show <skill_name>
    python3 skill_loader.py test
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Try to import yaml, fall back to manual frontmatter parsing
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class SkillDef:
    """A loaded skill definition."""
    name: str
    description: str
    content: str                       # The prompt template body
    source_path: Path | None = None
    when_to_use: str = ""
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    model: str | None = None           # Override model
    agent: str | None = None           # Restrict to agent
    context: str = "inline"            # "inline" or "fork"
    arguments: list[str] | None = None # Expected argument names
    hooks: dict | None = None          # Pre/post hooks
    is_bundled: bool = False
    is_hidden: bool = False
    is_enabled: bool = True

    def build_prompt(self, args: str = "", **kwargs) -> str:
        """
        Build the final prompt from the skill template.
        Substitutes ${1}, ${argName}, and ${SEAL_*} variables.
        """
        prompt = self.content

        # Positional args: ${1}, ${2}, etc.
        if args:
            parts = args.split(maxsplit=10)
            for i, part in enumerate(parts, 1):
                prompt = prompt.replace(f"${{{i}}}", part)
            # ${*} = all args
            prompt = prompt.replace("${*}", args)

        # Named kwargs
        for k, v in kwargs.items():
            prompt = prompt.replace(f"${{{k}}}", str(v))

        # SEAL variables
        prompt = prompt.replace("${SEAL_AGENT}", kwargs.get("agent", "ADA"))
        if self.source_path:
            prompt = prompt.replace("${SEAL_SKILL_DIR}", str(self.source_path.parent))

        return prompt

    def matches_agent(self, agent: str) -> bool:
        if self.agent is None:
            return True
        return self.agent.upper() == agent.upper()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown. Returns (metadata, body)."""
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    fm_text = text[3:end].strip()
    body = text[end + 4:].strip()

    if HAS_YAML:
        try:
            meta = yaml.safe_load(fm_text) or {}
        except Exception:
            meta = {}
    else:
        # Manual parsing for simple key: value pairs
        meta = {}
        for line in fm_text.split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                value = value.strip().strip('"').strip("'")
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                meta[key.strip()] = value

    return meta, body


def _load_skill_file(path: Path) -> SkillDef | None:
    """Load a single skill from a .md file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    meta, body = _parse_frontmatter(text)
    if not body.strip():
        return None

    name = meta.get("name", path.stem)
    allowed = meta.get("allowed-tools") or meta.get("allowed_tools")
    if isinstance(allowed, str):
        allowed = [t.strip() for t in allowed.split(",")]

    denied = meta.get("denied-tools") or meta.get("denied_tools")
    if isinstance(denied, str):
        denied = [t.strip() for t in denied.split(",")]

    arguments = meta.get("arguments")
    if isinstance(arguments, str):
        arguments = [a.strip() for a in arguments.split(",")]

    return SkillDef(
        name=name,
        description=meta.get("description", ""),
        content=body,
        source_path=path,
        when_to_use=meta.get("when_to_use", ""),
        allowed_tools=allowed,
        denied_tools=denied,
        model=meta.get("model"),
        agent=meta.get("agent"),
        context=meta.get("context", "inline"),
        arguments=arguments,
        hooks=meta.get("hooks"),
        is_hidden=bool(meta.get("hidden", False)),
        is_enabled=bool(meta.get("enabled", True)),
    )


class SkillLoader:
    """
    Discovers and loads skills from multiple directories.

    Priority (last wins): bundled → user → project
    """

    # Default search paths
    BUNDLED_DIR = Path(__file__).parent / "bundled"
    USER_DIR = Path.home() / ".seal" / "skills"
    PROJECT_DIRS = [".seal/skills", ".claude/skills"]  # relative to cwd

    def __init__(self, extra_dirs: list[Path] | None = None,
                 project_root: Path | None = None):
        self._dirs: list[Path] = []
        self._skills: dict[str, SkillDef] = {}

        # Build search path
        if self.BUNDLED_DIR.exists():
            self._dirs.append(self.BUNDLED_DIR)
        if self.USER_DIR.exists():
            self._dirs.append(self.USER_DIR)

        root = project_root or Path.cwd()
        for pd in self.PROJECT_DIRS:
            p = root / pd
            if p.exists():
                self._dirs.append(p)

        if extra_dirs:
            self._dirs.extend(d for d in extra_dirs if d.exists())

    def discover(self) -> list[SkillDef]:
        """Scan all directories and load skills. Returns list of skills."""
        self._skills.clear()

        for directory in self._dirs:
            for md_file in sorted(directory.rglob("*.md")):
                # Skip MEMORY.md, README.md, etc
                if md_file.name.upper() in ("MEMORY.MD", "README.MD", "CHANGELOG.MD"):
                    continue
                skill = _load_skill_file(md_file)
                if skill:
                    skill.is_bundled = (directory == self.BUNDLED_DIR)
                    self._skills[skill.name] = skill  # last-wins

        return list(self._skills.values())

    def get(self, name: str) -> SkillDef | None:
        """Get a skill by name."""
        if not self._skills:
            self.discover()
        return self._skills.get(name)

    def list_skills(self, agent: str | None = None,
                    include_hidden: bool = False) -> list[SkillDef]:
        """List all enabled skills, optionally filtered."""
        if not self._skills:
            self.discover()
        results = []
        for s in self._skills.values():
            if not s.is_enabled:
                continue
            if s.is_hidden and not include_hidden:
                continue
            if agent and not s.matches_agent(agent):
                continue
            results.append(s)
        return results

    def register_bundled(self, skill: SkillDef):
        """Register a programmatic (non-file) skill."""
        skill.is_bundled = True
        self._skills[skill.name] = skill


# ── Tests ───────────────────────────────────────────────────────────

def _run_tests():
    import tempfile, shutil

    test_dir = Path(tempfile.mkdtemp(prefix="skills_test_"))
    skills_dir = test_dir / "skills"
    skills_dir.mkdir()

    try:
        # T1: Parse frontmatter
        meta, body = _parse_frontmatter("---\nname: test\ndescription: A test skill\n---\nDo the thing")
        assert meta["name"] == "test"
        assert body == "Do the thing"
        print("PASS: T1 parse frontmatter ✓")

        # T2: No frontmatter
        meta, body = _parse_frontmatter("Just a prompt")
        assert meta == {}
        assert body == "Just a prompt"
        print("PASS: T2 no frontmatter ✓")

        # T3: Load skill file
        skill_file = skills_dir / "commit.md"
        skill_file.write_text(
            "---\nname: commit\ndescription: Generate a commit\nallowed-tools: Bash, Read\n---\n"
            "Review changes and create a commit with message: ${*}"
        )
        skill = _load_skill_file(skill_file)
        assert skill.name == "commit"
        assert skill.allowed_tools == ["Bash", "Read"]
        print("PASS: T3 load skill file ✓")

        # T4: Build prompt with args
        prompt = skill.build_prompt(args="fix: typo in readme")
        assert "fix: typo in readme" in prompt
        print("PASS: T4 build prompt with args ✓")

        # T5: Agent filter
        agent_skill_file = skills_dir / "ada_only.md"
        agent_skill_file.write_text("---\nname: ada_only\nagent: ADA\n---\nADA-specific task")
        skill2 = _load_skill_file(agent_skill_file)
        assert skill2.matches_agent("ADA")
        assert not skill2.matches_agent("JARVIS")
        print("PASS: T5 agent filter ✓")

        # T6: SkillLoader discover
        loader = SkillLoader(extra_dirs=[skills_dir])
        skills = loader.discover()
        assert len(skills) >= 2
        print(f"PASS: T6 discover ({len(skills)} skills) ✓")

        # T7: Get by name
        s = loader.get("commit")
        assert s is not None and s.name == "commit"
        print("PASS: T7 get by name ✓")

        # T8: List filtered by agent
        ada_skills = loader.list_skills(agent="JARVIS")
        ada_names = [s.name for s in ada_skills]
        assert "ada_only" not in ada_names
        print("PASS: T8 list filtered by agent ✓")

        # T9: Hidden skill
        hidden_file = skills_dir / "secret.md"
        hidden_file.write_text("---\nname: secret\nhidden: true\n---\nSecret stuff")
        loader.discover()
        visible = loader.list_skills()
        hidden = loader.list_skills(include_hidden=True)
        assert len(hidden) > len(visible)
        print("PASS: T9 hidden skill ✓")

        # T10: Disabled skill
        disabled_file = skills_dir / "disabled.md"
        disabled_file.write_text("---\nname: disabled\nenabled: false\n---\nDisabled")
        loader.discover()
        enabled = loader.list_skills(include_hidden=True)
        assert not any(s.name == "disabled" for s in enabled)
        print("PASS: T10 disabled skill ✓")

        # T11: Register bundled
        loader.register_bundled(SkillDef(name="loop", description="Recurring prompt", content="Run ${1} every ${2}"))
        assert loader.get("loop") is not None
        print("PASS: T11 register bundled ✓")

        # T12: SEAL variable substitution
        s = SkillDef(name="test", description="", content="Agent: ${SEAL_AGENT}")
        prompt = s.build_prompt(agent="JARVIS")
        assert "JARVIS" in prompt
        print("PASS: T12 SEAL variable substitution ✓")

        print(f"\n=== 12/12 TESTS PASARON ✓ ===")

    finally:
        shutil.rmtree(test_dir)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        _run_tests()
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        loader = SkillLoader()
        for s in loader.list_skills():
            print(f"  {s.name}: {s.description} {'[bundled]' if s.is_bundled else f'({s.source_path})'}")
    elif len(sys.argv) > 2 and sys.argv[1] == "show":
        loader = SkillLoader()
        s = loader.get(sys.argv[2])
        if s:
            print(f"Name: {s.name}\nDescription: {s.description}\nTools: {s.allowed_tools}\nAgent: {s.agent}\n---\n{s.content}")
        else:
            print(f"Skill '{sys.argv[2]}' not found")
    else:
        print("Usage: skill_loader.py test | list | show <name>")
