"""seal/skill_format.py — Portable SKILL.md format parser.

Defines the industry-standard SKILL.md frontmatter schema and provides
load/save/scan utilities for portable skill files.

A skill file is a Markdown document with a YAML frontmatter header:

    ---
    name: summarise
    description: Summarise any text in one paragraph
    version: 1.0.0
    author: team-seal
    triggers:
      - summarise
      - tl;dr
      - condense
    tags:
      - writing
      - productivity
    ---

    # Summarise

    Condense the following into a single, clear paragraph:

    ${*}

Internal gates (vote approval, sandbox execution) are NOT part of this
format — they live in soul DB and are applied separately by the runtime.

Stdlib only — no external deps, no PyYAML required.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

# ── YAML frontmatter parser (stdlib, no PyYAML) ────────────────────────────────

_FM_FENCE = re.compile(r"^---\s*$")
_KV_LINE  = re.compile(r"^(\w[\w\-]*)\s*:\s*(.*)")
_LIST_ITEM = re.compile(r"^\s{2,}-\s+(.*)")
_QUOTED    = re.compile(r'^"(.*)"$|^\'(.*)\'$')


def _unquote(s: str) -> str:
    m = _QUOTED.match(s.strip())
    return (m.group(1) or m.group(2)) if m else s.strip()


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split .md text into (frontmatter_dict, body).

    Returns ({}, full_text) if no valid frontmatter block found.
    """
    lines = text.splitlines(keepends=True)
    if not lines or not _FM_FENCE.match(lines[0].rstrip()):
        return {}, text

    fm_lines: list[str] = []
    end_idx   = -1
    for i, line in enumerate(lines[1:], start=1):
        if _FM_FENCE.match(line.rstrip()):
            end_idx = i
            break
        fm_lines.append(line)

    if end_idx == -1:
        return {}, text

    body = "".join(lines[end_idx + 1:]).lstrip("\n")
    data = _parse_yaml_subset(fm_lines)
    return data, body


def _parse_yaml_subset(lines: list[str]) -> dict[str, Any]:
    """Parse a restricted YAML subset: scalars and string lists."""
    result: dict[str, Any] = {}
    current_key: Optional[str] = None
    current_list: Optional[list[str]] = None

    for raw in lines:
        line = raw.rstrip("\n")

        # List item for the current key
        m = _LIST_ITEM.match(line)
        if m and current_list is not None:
            current_list.append(_unquote(m.group(1)))
            continue

        # New key-value
        m = _KV_LINE.match(line)
        if m:
            if current_key and current_list is not None:
                result[current_key] = current_list
                current_list = None

            key  = m.group(1)
            val  = m.group(2).strip()

            if val == "" or val == "[]":
                # Empty list (next lines will be items) or explicit empty
                current_key  = key
                current_list = [] if val == "" else []
                if val == "[]":
                    result[key] = []
                    current_key  = None
                    current_list = None
            else:
                current_key  = key
                current_list = None
                result[key]  = _coerce(val)

    # Flush trailing list
    if current_key and current_list is not None:
        result[current_key] = current_list

    return result


def _coerce(val: str) -> Any:
    v = _unquote(val)
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    return v


# ── Skill dataclasses ──────────────────────────────────────────────────────────


@dataclass
class SkillManifest:
    """Portable metadata header for a SEAL skill file."""

    name:        str
    description: str
    version:     str                    = "1.0.0"
    author:      str                    = ""
    triggers:    list[str]              = field(default_factory=list)
    tags:        list[str]              = field(default_factory=list)

    # Extended optional fields (preserved on round-trip, ignored by runtime core)
    argument_hint: str                  = ""
    user_invocable: bool                = True
    model:          Optional[str]       = None
    extra:          dict[str, Any]      = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillManifest":
        known = {
            "name":           str(d.get("name", "")),
            "description":    str(d.get("description", "")),
            "version":        str(d.get("version", "1.0.0")),
            "author":         str(d.get("author", "")),
            "triggers":       _ensure_list(d.get("triggers", [])),
            "tags":           _ensure_list(d.get("tags", [])),
            "argument_hint":  str(d.get("argument-hint", d.get("argument_hint", ""))),
            "user_invocable": bool(d.get("user-invocable", d.get("user_invocable", True))),
            "model":          d.get("model") or None,
        }
        used = {"name", "description", "version", "author", "triggers", "tags",
                "argument-hint", "argument_hint", "user-invocable", "user_invocable", "model"}
        extra = {k: v for k, v in d.items() if k not in used}
        return cls(**known, extra=extra)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name":        self.name,
            "description": self.description,
            "version":     self.version,
        }
        if self.author:
            d["author"] = self.author
        if self.triggers:
            d["triggers"] = self.triggers
        if self.tags:
            d["tags"] = self.tags
        if self.argument_hint:
            d["argument-hint"] = self.argument_hint
        if not self.user_invocable:
            d["user-invocable"] = False
        if self.model:
            d["model"] = self.model
        d.update(self.extra)
        return d


@dataclass
class Skill:
    """A fully loaded skill: manifest + prompt body."""

    manifest: SkillManifest
    body:     str
    source:   Optional[Path] = None

    # ── Convenience accessors ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def description(self) -> str:
        return self.manifest.description

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def triggers(self) -> list[str]:
        return self.manifest.triggers

    @property
    def tags(self) -> list[str]:
        return self.manifest.tags

    # ── Argument interpolation ─────────────────────────────────────────────

    def render(self, *args: str, **kwargs: str) -> str:
        """Interpolate ${1}…${N}, ${*}, and ${name} placeholders."""
        text = self.body
        for i, val in enumerate(args, 1):
            text = text.replace(f"${{{i}}}", val)
        text = text.replace("${*}", " ".join(args))
        for key, val in kwargs.items():
            text = text.replace(f"${{{key}}}", val)
        return text

    # ── Serialisation ──────────────────────────────────────────────────────

    def to_markdown(self) -> str:
        return render_skill_md(self)

    @classmethod
    def from_markdown(cls, text: str, source: Optional[Path] = None) -> "Skill":
        return load_skill_text(text, source=source)


# ── Serialization helpers ──────────────────────────────────────────────────────


def render_skill_md(skill: Skill) -> str:
    """Serialize a Skill back to the canonical SKILL.md string."""
    d = skill.manifest.to_dict()
    lines = ["---"]
    for key, val in d.items():
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {item}")
        elif isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        else:
            safe = str(val)
            if any(c in safe for c in (':', '#', '[', ']', '{', '}')):
                safe = f'"{safe}"'
            lines.append(f"{key}: {safe}")
    lines.append("---")
    lines.append("")
    body = skill.body.rstrip("\n")
    return "\n".join(lines) + ("\n" + body if body else "")


def _ensure_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str) and val:
        return [v.strip() for v in val.split(",") if v.strip()]
    return []


# ── Load / save ────────────────────────────────────────────────────────────────


class SkillParseError(ValueError):
    pass


def load_skill_text(text: str, source: Optional[Path] = None) -> Skill:
    """Parse a SKILL.md string into a Skill object."""
    fm, body = _parse_frontmatter(text)
    if not fm.get("name"):
        raise SkillParseError(
            f"missing required 'name' field in frontmatter"
            + (f" ({source})" if source else "")
        )
    if not fm.get("description"):
        raise SkillParseError(
            f"missing required 'description' field in frontmatter"
            + (f" ({source})" if source else "")
        )
    manifest = SkillManifest.from_dict(fm)
    return Skill(manifest=manifest, body=body, source=source)


def load_skill_file(path: Path) -> Skill:
    """Load a single SKILL.md file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillParseError(f"cannot read {path}: {exc}") from exc
    return load_skill_text(text, source=path)


def save_skill_file(skill: Skill, path: Path) -> None:
    """Write a Skill to a .md file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skill.to_markdown() + "\n", encoding="utf-8")


# ── Catalog ────────────────────────────────────────────────────────────────────

_MD_GLOB = "*.md"


class SkillCatalog:
    """Scans one or more directories and indexes all valid SKILL.md files.

    Skills from later directories override same-named skills from earlier ones
    (project > user > bundled precedence).
    """

    def __init__(self, dirs: Optional[list[Path]] = None) -> None:
        self._skills: dict[str, Skill] = {}
        for d in (dirs or []):
            self._load_dir(d)

    # ── Mutation ───────────────────────────────────────────────────────────

    def _load_dir(self, directory: Path) -> int:
        count = 0
        if not directory.is_dir():
            return 0
        for path in sorted(directory.glob(_MD_GLOB)):
            try:
                skill = load_skill_file(path)
                self._skills[skill.name] = skill
                count += 1
            except SkillParseError:
                pass
        return count

    def add(self, skill: Skill) -> None:
        """Register a skill programmatically (e.g. from a test or install step)."""
        self._skills[skill.name] = skill

    def remove(self, name: str) -> bool:
        return bool(self._skills.pop(name, None))

    # ── Query ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def search(self, query: str) -> list[Skill]:
        """Return skills whose name, description, triggers, or tags contain query."""
        q = query.lower()
        results = []
        for skill in self._skills.values():
            haystack = " ".join([
                skill.name,
                skill.description,
                " ".join(skill.triggers),
                " ".join(skill.tags),
            ]).lower()
            if q in haystack:
                results.append(skill)
        return sorted(results, key=lambda s: s.name)

    def by_tag(self, tag: str) -> list[Skill]:
        t = tag.lower()
        return [s for s in self._skills.values() if t in [x.lower() for x in s.tags]]

    def by_trigger(self, trigger: str) -> list[Skill]:
        t = trigger.lower()
        return [s for s in self._skills.values() if t in [x.lower() for x in s.triggers]]

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills


# ── Default catalog dirs ───────────────────────────────────────────────────────

def default_skill_dirs() -> list[Path]:
    """Return the standard skill search path (bundled → user → project)."""
    seal_pkg = Path(__file__).resolve().parent
    return [
        seal_pkg / "bundled_skills",                      # bundled
        Path.home() / ".seal" / "skills",                 # user global
        Path.cwd() / ".seal" / "skills",                  # project-local
    ]


def load_default_catalog() -> SkillCatalog:
    """Load the standard catalog from all default directories."""
    return SkillCatalog(default_skill_dirs())


# ── Minimal CLI ───────────────────────────────────────────────────────────────


def _cli(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="seal skills")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list all available skills")

    sh = sub.add_parser("show", help="show a skill's frontmatter + body")
    sh.add_argument("name")

    imp = sub.add_parser("install", help="install a skill file into ~/.seal/skills/")
    imp.add_argument("file", type=Path)

    args = p.parse_args(argv)
    catalog = load_default_catalog()

    if args.cmd == "list":
        if not catalog:
            print("no skills found")
            return 0
        fmt = "{:<20} {:<10} {}"
        print(fmt.format("NAME", "VERSION", "DESCRIPTION"))
        print("-" * 72)
        for s in catalog.all():
            print(fmt.format(s.name, s.version, s.description[:42]))
        return 0

    if args.cmd == "show":
        skill = catalog.get(args.name)
        if not skill:
            print(f"skill '{args.name}' not found", file=sys.stderr)
            return 1
        print(skill.to_markdown())
        return 0

    if args.cmd == "install":
        src = args.file
        try:
            skill = load_skill_file(src)
        except SkillParseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        dest = Path.home() / ".seal" / "skills" / src.name
        save_skill_file(skill, dest)
        print(f"installed '{skill.name}' → {dest}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
