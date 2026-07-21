"""Local evaluation CLI. It never writes to SOUL DB."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from .adapters.text import TextAdapter
from .contracts import Scope, Sensitivity
from .engine import IngestionEngine


DEFAULT_TENANT = UUID("00000000-0000-0000-0000-000000000001")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SOUL Universal Ingestion Engine — local extractive CLI")
    parser.add_argument("path", nargs="?", help="UTF-8/UTF-16 text file; stdin when omitted")
    parser.add_argument("--profile", default="generic_v1")
    parser.add_argument("--owner", default="ADA")
    parser.add_argument("--tenant-id", type=UUID, default=DEFAULT_TENANT)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.path:
        path = Path(args.path).resolve()
        if not path.is_file():
            raise SystemExit(f"not a regular file: {path}")
        content = path.read_bytes()
        source_ref = f"file:{path.name}"
    else:
        content = sys.stdin.buffer.read()
        source_ref = None
    artifact = TextAdapter().acquire(
        content,
        tenant_id=args.tenant_id,
        owner_agent=args.owner,
        scope=Scope.PRIVATE,
        source_ref=source_ref,
        sensitivity=Sensitivity.INTERNAL,
    )
    result = IngestionEngine().process(artifact, profile_id=args.profile)
    if args.as_json:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
    else:
        print(result.derivations[0].content)
        print(
            f"\n[SUIE] state={result.state} document={result.document.document_id} "
            f"coverage={result.derivations[0].coverage.covered_sections}/"
            f"{result.derivations[0].coverage.total_sections}",
            file=sys.stderr,
        )
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
