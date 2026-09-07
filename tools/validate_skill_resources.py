#!/usr/bin/env python3
"""Syntax-check executable resources bundled with active SOUL skills."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = ("skills", "tools/skills", ".agents/skills", ".claude/skills")


def validate(roots: list[Path]) -> dict:
    files = {"python": [], "shell": [], "javascript": []}
    for root in roots:
        if not root.is_dir():
            continue
        files["python"].extend(sorted(root.rglob("*.py")))
        files["shell"].extend(sorted(root.rglob("*.sh")))
        files["javascript"].extend(sorted(root.rglob("*.js")))
        files["javascript"].extend(sorted(root.rglob("*.mjs")))
    findings = []
    for path in files["python"]:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            findings.append({"kind": "python", "path": str(path), "error": str(exc)})
    for kind, command in (("shell", ["bash", "-n"]), ("javascript", ["node", "--check"])):
        for path in files[kind]:
            proc = subprocess.run(command + [str(path)], text=True, capture_output=True, check=False)
            if proc.returncode:
                findings.append({"kind": kind, "path": str(path), "error": (proc.stderr or proc.stdout).strip()})
    return {
        "schema": "soul-skill-resource-syntax-v1",
        "counts": {kind: len(paths) for kind, paths in files.items()},
        "passed": not findings,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", dest="roots")
    args = parser.parse_args()
    roots = [REPO / item for item in (args.roots or DEFAULT_ROOTS)]
    result = validate(roots)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
