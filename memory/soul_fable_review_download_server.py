#!/usr/bin/env python3
"""Tiny download server for SOUL/Fable review files.

Unlike ``python -m http.server``, this forces downloadable files to be served
with ``Content-Disposition: attachment`` so browsers show/save them instead of
opening markdown inline.
"""

from __future__ import annotations

import argparse
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DOWNLOAD_EXTENSIONS = {
    ".md",
    ".txt",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".json",
    ".zip",
}


class DownloadHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        parsed = urlparse(self.path)
        filename = Path(unquote(parsed.path)).name
        suffix = Path(filename).suffix.lower()
        if filename and suffix in DOWNLOAD_EXTENSIONS:
            safe = filename.replace('"', "")
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
            self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[soul-fable-download] {self.address_string()} {fmt % args}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8854)
    args = parser.parse_args()

    os.chdir(args.directory)
    server = ThreadingHTTPServer((args.host, args.port), DownloadHandler)
    print(f"serving {args.directory} on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

