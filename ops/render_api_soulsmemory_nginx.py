#!/usr/bin/env python3
"""Render the staged api.soulsmemory.com Nginx template atomically.

This command only writes a config artifact.  It never installs, enables,
reloads, restarts, requests certificates, or touches DNS.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


TOKENS = {
    "__TLS_CERTIFICATE__": "certificate",
    "__TLS_CERTIFICATE_KEY__": "certificate_key",
    "__ACCESS_LOG__": "access_log",
    "__ERROR_LOG__": "error_log",
}


def _nginx_path(value: Path) -> str:
    path = str(value)
    # Values are inserted as unquoted Nginx directive arguments.  Reject every
    # character that can split or terminate the argument instead of trying to
    # invent an escaping dialect here.
    if any(char.isspace() for char in path) or any(
        char in path for char in (";", "{", "}", "'", '"', "\\")
    ):
        raise ValueError(f"unsafe Nginx path: {path!r}")
    if not path.startswith("/"):
        raise ValueError(f"Nginx path must be absolute: {path!r}")
    return path


def render_config(
    template: str,
    *,
    certificate: Path,
    certificate_key: Path,
    access_log: Path,
    error_log: Path,
) -> str:
    values = {
        "certificate": _nginx_path(certificate),
        "certificate_key": _nginx_path(certificate_key),
        "access_log": _nginx_path(access_log),
        "error_log": _nginx_path(error_log),
    }
    rendered = template
    for token, name in TOKENS.items():
        if rendered.count(token) != 1:
            raise ValueError(f"template must contain {token} exactly once")
        rendered = rendered.replace(token, values[name])
    if "__" in rendered:
        raise ValueError("unresolved template token remains")
    return rendered


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent
    parser.add_argument(
        "--template",
        type=Path,
        default=base / "nginx" / "api.soulsmemory.com.conf.template",
    )
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--certificate-key", type=Path, required=True)
    parser.add_argument(
        "--access-log",
        type=Path,
        default=Path("/var/log/nginx/api.soulsmemory.com.access.log"),
    )
    parser.add_argument(
        "--error-log",
        type=Path,
        default=Path("/var/log/nginx/api.soulsmemory.com.error.log"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-missing-cert",
        action="store_true",
        help="test-only: render paths before certificate provisioning",
    )
    args = parser.parse_args()

    if not args.allow_missing_cert:
        for label, path in (
            ("certificate", args.certificate),
            ("certificate key", args.certificate_key),
        ):
            if not path.is_file():
                parser.error(f"{label} does not exist or is not a file: {path}")

    rendered = render_config(
        args.template.read_text(encoding="utf-8"),
        certificate=args.certificate,
        certificate_key=args.certificate_key,
        access_log=args.access_log,
        error_log=args.error_log,
    )
    atomic_write(args.output, rendered)
    print(f"rendered={args.output} bytes={len(rendered.encode('utf-8'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
