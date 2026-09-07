#!/usr/bin/env python3
"""Deterministic, read-only audit for the active SOUL skill surface.

The default roots deliberately exclude vendored reference repositories, virtual
environments and plugin caches.  Findings are machine-readable and the process
fails closed on structural errors; warnings remain visible without pretending
that every legacy convention is a broken skill.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = ("skills", "tools/skills", ".agents/skills", ".claude/skills")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
DESTRUCTIVE_RE = re.compile(
    r"(?:\brm\s+-rf\b|\bDROP\s+(?:TABLE|SCHEMA|DATABASE|ROLE)\b|"
    r"\bTRUNCATE\s+(?:TABLE\s+)?(?:[\"`][^\"`\n]+[\"`]|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)"
    r"\s*(?:;|`|CASCADE\b|RESTRICT\b|$)|\bDELETE\s+FROM\b)",
    re.IGNORECASE | re.MULTILINE,
)
GATE_RE = re.compile(
    r"confirm|approval|aprobaci[oó]n|scope|alcance|count|conteo|rollback|"
    r"William|destructive|destructiv[ao]|mktemp\s+-d|/var/lib/apt/lists/\*|"
    r"deny(?:list)?|block(?:ed|ing)?|forbid|prevent|prohibit",
    re.IGNORECASE,
)
STALE_PATTERNS = {
    "qdrant-retired": re.compile(r"\bqdrant\b", re.IGNORECASE),
    "legacy-port-8766": re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):8766|\bport\s+8766\b", re.IGNORECASE),
    "claude-only-skill-path": re.compile(r"(?:~|/home/[^/]+)?/\.claude/skills/", re.IGNORECASE),
}
RETIRED_CONTEXT_RE = re.compile(r"retired|retirad[oa]|removed|no usar|never use|must remain down|excluir|exclude", re.IGNORECASE)
PRIVILEGED_DSN_RE = re.compile(r"postgres(?:ql)?://seal:[^\s`@]+@", re.IGNORECASE)
PLAIN_PIP_RE = re.compile(r"(?m)^\s*(?:sudo\s+)?pip(?:3)?\s+install\b")
CURL_PIPE_SHELL_RE = re.compile(r"curl\b[^\n|]*\|\s*(?:sudo\s+)?(?:bash|sh)\b", re.IGNORECASE)
IGNORED_LINK_SCHEMES = ("http://", "https://", "mailto:", "data:", "app://")


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    message: str


@dataclass
class SkillRecord:
    path: str
    root: str
    name: str | None
    description: str | None
    sha256: str
    lines: int
    structural_status: str = "PASS"
    errors: int = 0
    warnings: int = 0


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def finding(severity: str, code: str, path: Path | str, message: str) -> Finding:
    return Finding(severity, code, rel(path) if isinstance(path, Path) else path, message)


def discover(roots: Iterable[Path]) -> list[tuple[Path, Path]]:
    discovered: list[tuple[Path, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            if any(part in {".git", ".venv", "venv", "node_modules", "__pycache__"} for part in path.parts):
                continue
            discovered.append((root, path))
    return discovered


def parse_frontmatter(path: Path, text: str) -> tuple[dict[str, Any] | None, str, list[Finding]]:
    out: list[Finding] = []
    if not text.startswith("---\n"):
        return None, text, [finding("ERROR", "frontmatter-missing", path, "SKILL.md must start with ---")]
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, text, [finding("ERROR", "frontmatter-unclosed", path, "frontmatter closing delimiter is missing")]
    raw = text[4:end]
    body = text[end + 5 :]
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, body, [finding("ERROR", "frontmatter-invalid-yaml", path, str(exc).splitlines()[0])]
    if not isinstance(meta, dict):
        return None, body, [finding("ERROR", "frontmatter-not-mapping", path, "frontmatter must be a YAML mapping")]
    return meta, body, out


def prose_without_code(text: str) -> str:
    """Remove fenced and inline code before interpreting Markdown links."""
    without_fences = re.sub(r"```.*?```|~~~.*?~~~", "", text, flags=re.DOTALL)
    return re.sub(r"`[^`]*`", "", without_fences)


def text_without_dockerfile_fences(text: str) -> str:
    """Drop Dockerfile examples before applying host-only installation rules."""
    out: list[str] = []
    fence_language: str | None = None
    for line in text.splitlines(keepends=True):
        match = re.match(r"^\s*(```|~~~)\s*([^\s`]*)", line)
        if match:
            if fence_language is None:
                fence_language = match.group(2).strip().lower()
            else:
                fence_language = None
            out.append("\n")
            continue
        if fence_language in {"dockerfile", "docker"}:
            out.append("\n")
        else:
            out.append(line)
    return "".join(out)


def allowed_keys(root: Path) -> set[str]:
    if rel(root).startswith("tools/skills"):
        return {"name", "description", "version", "author", "license", "metadata"}
    return {"name", "description"}


def local_link_target(raw: str) -> str | None:
    target = raw.strip().split(maxsplit=1)[0].strip("<>")
    if not target or target.startswith("#") or target.lower().startswith(IGNORED_LINK_SCHEMES):
        return None
    if "{" in target or "}" in target or "$" in target:
        return None
    return target.split("#", 1)[0]


def audit_skill(root: Path, path: Path) -> tuple[SkillRecord, list[Finding]]:
    findings: list[Finding] = []
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeDecodeError) as exc:
        record = SkillRecord(rel(path), rel(root), None, None, "", 0, "FAIL", 1, 0)
        return record, [finding("ERROR", "unreadable", path, str(exc))]

    meta, body, parse_findings = parse_frontmatter(path, text)
    findings.extend(parse_findings)
    name = meta.get("name") if meta else None
    description = meta.get("description") if meta else None

    if meta is not None:
        if not isinstance(name, str) or not name.strip():
            findings.append(finding("ERROR", "name-missing", path, "frontmatter.name is required"))
        elif len(name) > 64 or not NAME_RE.fullmatch(name):
            findings.append(finding("ERROR", "name-invalid", path, "name must be lowercase hyphen-case and <=64 chars"))
        elif path.parent.name != name:
            findings.append(finding("WARNING", "directory-name-mismatch", path, f"directory={path.parent.name!r}, name={name!r}"))

        if not isinstance(description, str) or not description.strip():
            findings.append(finding("ERROR", "description-missing", path, "frontmatter.description is required"))
        elif len(description) > 1024:
            findings.append(finding("ERROR", "description-too-long", path, f"description has {len(description)} chars"))

        extras = sorted(set(meta) - allowed_keys(root))
        if extras:
            findings.append(finding("WARNING", "frontmatter-extra-keys", path, f"non-canonical keys: {', '.join(extras)}"))

    if not body.strip():
        findings.append(finding("ERROR", "body-empty", path, "skill body is empty"))
    line_count = len(text.splitlines())
    if line_count > 500:
        findings.append(finding("WARNING", "body-too-long", path, f"{line_count} lines; use progressive disclosure"))

    for raw_target in LINK_RE.findall(prose_without_code(body)):
        target = local_link_target(raw_target)
        if target is None:
            continue
        candidate = (path.parent / target).resolve()
        if not candidate.exists():
            findings.append(finding("ERROR", "broken-local-link", path, f"missing local target: {target}"))

    for code, pattern in STALE_PATTERNS.items():
        matches = []
        for line in text.splitlines():
            if pattern.search(line) and not RETIRED_CONTEXT_RE.search(line):
                matches.append(line)
        if matches:
            findings.append(finding("WARNING", code, path, "legacy/retired reference requires human review"))

    if PRIVILEGED_DSN_RE.search(text):
        findings.append(finding("ERROR", "embedded-privileged-dsn", path, "embedded seal credential is forbidden; use a protected least-privilege DSN"))
    if PLAIN_PIP_RE.search(text_without_dockerfile_fences(text)):
        findings.append(finding("WARNING", "plain-pip-install", path, "use python -m venv and python -m pip under PEP 668"))
    unsafe_pipe_lines = [
        line for line in text.splitlines()
        if CURL_PIPE_SHELL_RE.search(line) and not re.search(r"avoid|never|do not|no usar|prohibit", line, re.IGNORECASE)
    ]
    if unsafe_pipe_lines:
        findings.append(finding("WARNING", "curl-pipe-shell", path, "download, verify provenance/checksum, then execute explicitly"))
    if path.parent.name == "vllm" and "Never use vLLM on DGX Spark" not in text:
        findings.append(finding("ERROR", "vllm-dgx-contract-missing", path, "SOUL requires a fail-closed DGX Spark prohibition"))

    for match in DESTRUCTIVE_RE.finditer(body):
        context = body[max(0, match.start() - 500) : min(len(body), match.end() + 500)]
        if not GATE_RE.search(context):
            findings.append(finding("WARNING", "destructive-without-gate", path, f"unguarded pattern near {match.group(0)!r}"))
            break

    errors = sum(item.severity == "ERROR" for item in findings)
    warnings = sum(item.severity == "WARNING" for item in findings)
    record = SkillRecord(
        path=rel(path),
        root=rel(root),
        name=name if isinstance(name, str) else None,
        description=description if isinstance(description, str) else None,
        sha256=hashlib.sha256(raw).hexdigest(),
        lines=line_count,
        structural_status="FAIL" if errors else "PASS",
        errors=errors,
        warnings=warnings,
    )
    return record, findings


def audit(roots: list[Path]) -> dict[str, Any]:
    records: list[SkillRecord] = []
    findings: list[Finding] = []
    discovered = discover(roots)
    by_name: dict[str, list[SkillRecord]] = defaultdict(list)
    by_hash: dict[str, list[SkillRecord]] = defaultdict(list)
    skill_dirs = {path.parent.resolve() for _, path in discovered}

    for root, path in discovered:
        record, item_findings = audit_skill(root, path)
        records.append(record)
        findings.extend(item_findings)
        if record.name:
            by_name[record.name].append(record)
        if record.sha256:
            by_hash[record.sha256].append(record)
        if path.is_symlink() or path.parent.is_symlink():
            findings.append(finding("WARNING", "skill-symlink", path, "symlinked skill requires a single canonical owner"))
        parent = path.parent.parent.resolve()
        while parent != root.resolve() and parent != parent.parent:
            if parent in skill_dirs:
                findings.append(finding("WARNING", "nested-skill", path, f"nested below skill directory {rel(parent)}"))
                break
            parent = parent.parent

    for name, group in sorted(by_name.items()):
        if len(group) > 1:
            paths = ", ".join(item.path for item in group)
            for item in group:
                findings.append(Finding("WARNING", "duplicate-name", item.path, f"name {name!r} appears at: {paths}"))
    for digest, group in sorted(by_hash.items()):
        if len(group) > 1:
            paths = ", ".join(item.path for item in group)
            for item in group:
                findings.append(Finding("WARNING", "duplicate-content", item.path, f"sha256 {digest[:12]} shared by: {paths}"))

    # Recompute record totals after cross-skill findings.
    cross: dict[str, list[Finding]] = defaultdict(list)
    for item in findings:
        cross[item.path].append(item)
    for record in records:
        record.errors = sum(item.severity == "ERROR" for item in cross[record.path])
        record.warnings = sum(item.severity == "WARNING" for item in cross[record.path])
        record.structural_status = "FAIL" if record.errors else "PASS"

    counts = {
        "skills": len(records),
        "structural_pass": sum(record.structural_status == "PASS" for record in records),
        "structural_fail": sum(record.structural_status == "FAIL" for record in records),
        "errors": sum(item.severity == "ERROR" for item in findings),
        "warnings": sum(item.severity == "WARNING" for item in findings),
        "duplicate_name_groups": sum(len(group) > 1 for group in by_name.values()),
        "duplicate_content_groups": sum(len(group) > 1 for group in by_hash.values()),
    }
    return {
        "schema": "soul-skill-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": [rel(root) for root in roots],
        "counts": counts,
        "skills": [asdict(record) for record in records],
        "findings": [asdict(item) for item in sorted(findings, key=lambda x: (x.severity != "ERROR", x.code, x.path))],
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", dest="roots", help="skill root relative to repository; repeatable")
    parser.add_argument("--output", type=Path, help="write full audit report JSON")
    parser.add_argument("--catalog", type=Path, help="write compact deterministic skill catalog JSON")
    parser.add_argument("--strict-warnings", action="store_true", help="also fail when warnings exist")
    args = parser.parse_args()

    roots = [REPO / item for item in (args.roots or DEFAULT_ROOTS)]
    report = audit(roots)
    if args.output:
        write_json(args.output, report)
    if args.catalog:
        catalog = {
            "schema": "soul-skill-catalog-v1",
            "scope": report["scope"],
            "counts": report["counts"],
            "skills": report["skills"],
        }
        write_json(args.catalog, catalog)
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
    if report["counts"]["errors"]:
        return 1
    if args.strict_warnings and report["counts"]["warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
