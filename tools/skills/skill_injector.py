"""Skill injector — loads SEAL skill packs and injects them into agent prompts.

This is the component that makes skills DEMOSTRABLE: it bridges the SKILL.md
files (now native in tools/skills/) with the agent runtime.

Usage:
    injector = SkillInjector()
    skill = injector.load("research-paper-writing")
    system_prompt = injector.inject(base_prompt, skill)

Skills are looked up from:
  1. soul_v3.skills DB table (name → skill_path)
  2. Direct file path fallback (tools/skills/<name>/SKILL.md)

"inteligencia artificial = python puro" — William, 2026-04-28.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SEAL_SKILLS_DIR = Path(__file__).resolve().parent


class SkillNotFoundError(Exception):
    """Raised when a skill cannot be located."""


@dataclass
class Skill:
    name: str
    content: str
    path: Path

    @property
    def header(self) -> str:
        """First non-empty line — usually the skill title."""
        for line in self.content.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                return line
        return self.name

    def inject_into(self, system_prompt: str, position: str = "append") -> str:
        """Return system_prompt with skill content merged in.

        position: "append" (default) | "prepend"
        """
        block = f"\n\n---\n# Skill: {self.name}\n{self.content}\n---"
        if position == "prepend":
            return block.lstrip() + "\n\n" + system_prompt
        return system_prompt + block


class SkillInjector:
    """Loads skill packs from SEAL native storage and injects them into prompts.

    DB-backed: resolves skill_path from soul_v3.skills by name.
    File-backed: falls back to tools/skills/<name>/SKILL.md if DB is unavailable.
    """

    def __init__(self, skills_dir: Optional[Path] = None) -> None:
        self._dir = skills_dir or SEAL_SKILLS_DIR
        self._pool = None

    def load(self, name: str) -> Skill:
        """Load a skill by name. Tries DB first, then filesystem scan."""
        path = self._resolve_path(name)
        if path is None or not path.exists():
            raise SkillNotFoundError(f"skill {name!r} not found in {self._dir}")
        content = path.read_text(encoding="utf-8")
        return Skill(name=name, content=content, path=path)

    def load_path(self, skill_path: str) -> Skill:
        """Load a skill directly from a known file path."""
        path = Path(skill_path)
        if not path.exists():
            raise SkillNotFoundError(f"skill file not found: {skill_path}")
        name = path.parent.name
        return Skill(name=name, content=path.read_text(encoding="utf-8"), path=path)

    def inject(
        self,
        system_prompt: str,
        skill: Skill,
        position: str = "append",
    ) -> str:
        """Return system_prompt with skill injected."""
        return skill.inject_into(system_prompt, position=position)

    def inject_by_name(
        self,
        system_prompt: str,
        skill_name: str,
        position: str = "append",
    ) -> str:
        """Convenience: load + inject in one call."""
        return self.inject(system_prompt, self.load(skill_name), position)

    def list_available(self) -> list[str]:
        """Return skill names discoverable from the skills directory."""
        names: list[str] = []
        for skill_md in sorted(self._dir.rglob("SKILL.md")):
            names.append(skill_md.parent.name)
        return names

    def _resolve_path(self, name: str) -> Optional[Path]:
        """Resolve a skill name to its SKILL.md path."""
        # Direct subdir match: tools/skills/<name>/SKILL.md
        direct = self._dir / name / "SKILL.md"
        if direct.exists():
            return direct

        # Deep scan: any category/<name>/SKILL.md
        for candidate in self._dir.rglob(f"{name}/SKILL.md"):
            return candidate

        return None
