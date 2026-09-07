#!/usr/bin/env python3
"""Compare legacy SOUL API and new SOUL Memory SDK API before cutover."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


LEGACY_DEFAULT = "http://127.0.0.1:8767"
SDK_DEFAULT = "http://127.0.0.1:8768"


@dataclass(frozen=True)
class Endpoint:
    path: str
    methods: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{','.join(self.methods)} {self.path}"


def fetch_openapi(base_url: str) -> dict[str, Any]:
    response = requests.get(f"{base_url.rstrip('/')}/openapi.json", timeout=5)
    response.raise_for_status()
    return response.json()


def endpoints(openapi: dict[str, Any]) -> list[Endpoint]:
    items: list[Endpoint] = []
    for path, methods in sorted((openapi.get("paths") or {}).items()):
        http_methods = tuple(
            sorted(method.upper() for method in methods if method.lower() in {"get", "post", "put", "patch", "delete"})
        )
        items.append(Endpoint(path=path, methods=http_methods))
    return items


def compare(legacy_url: str, sdk_url: str) -> dict[str, Any]:
    legacy_api = fetch_openapi(legacy_url)
    sdk_api = fetch_openapi(sdk_url)
    legacy = endpoints(legacy_api)
    sdk = endpoints(sdk_api)
    legacy_keys = {e.key for e in legacy}
    sdk_keys = {e.key for e in sdk}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legacy_url": legacy_url,
        "sdk_url": sdk_url,
        "legacy_title": (legacy_api.get("info") or {}).get("title"),
        "sdk_title": (sdk_api.get("info") or {}).get("title"),
        "legacy_only": sorted(legacy_keys - sdk_keys),
        "sdk_only": sorted(sdk_keys - legacy_keys),
        "shared": sorted(legacy_keys & sdk_keys),
    }


def render_markdown(data: dict[str, Any]) -> str:
    def section(title: str, rows: list[str]) -> str:
        lines = [f"## {title}", ""]
        if rows:
            lines.extend(f"- `{row}`" for row in rows)
        else:
            lines.append("- none")
        lines.append("")
        return "\n".join(lines)

    md = [
        "# SOUL Memory SDK Cutover Audit",
        "",
        f"- Generated: `{data['generated_at']}`",
        f"- Legacy API: `{data['legacy_url']}` (`{data['legacy_title']}`)",
        f"- SDK API: `{data['sdk_url']}` (`{data['sdk_title']}`)",
        "",
        "## Conclusion",
        "",
        "`:8767` cannot be replaced directly by the new SDK API without losing legacy soul/auth/widget/installer endpoints. Recommended cutover is a gateway or merged app that preserves legacy routes and routes only memory endpoints to the new tenant-safe runtime.",
        "",
        section("Legacy Only", data["legacy_only"]),
        section("SDK Only", data["sdk_only"]),
        section("Shared", data["shared"]),
        "## Recommended Path",
        "",
        "Use a two-service gateway cutover, not a direct replacement:",
        "",
        "1. Keep legacy SOUL API behavior intact.",
        "2. Keep SDK Memory API alive on `127.0.0.1:8768`.",
        "3. Keep the gateway in staging on `127.0.0.1:8780` until all gates pass.",
        "4. Route SDK memory endpoints to `127.0.0.1:8768`:",
        "   - `/v1/memories`",
        "   - `/v1/memories/{id}`",
        "   - `/v1/memory`",
        "   - `/v1/memory/{id}`",
        "   - `/v1/memory/search`",
        "   - `/v1/recall`",
        "5. Route all legacy-only endpoints to the old SOUL API.",
        "6. Move public `:8767` only after legacy is reachable on an internal fallback port or container network address.",
        "",
        "Do not stop or replace the old `soul-api-server` container until the fallback route is proven with all legacy endpoints above.",
        "",
        "## Gateway Staging",
        "",
        "- Module: `memory/soul_memory_sdk_gateway.py`",
        "- Tests: `memory/test_soul_memory_sdk_gateway.py`",
        "- Service unit: `memory/seal-memory-sdk-gateway.service`",
        "- Staging URL: `http://127.0.0.1:8780`",
        "",
        "The gateway is path-conservative. Only memory SDK routes go to the SDK upstream. `/health` belongs to the gateway and reports both upstreams. Legacy auth, installer, widget, admin, and soul routes stay on the old API.",
        "",
        "## Cutover Gates",
        "",
        "Before touching `:8767`, all of these must pass:",
        "",
        "- Gateway health: `GET http://127.0.0.1:8780/health` reports SDK and legacy OK.",
        "- Legacy health: `GET http://127.0.0.1:<legacy_fallback>/health` returns OK after the old API is moved behind the gateway.",
        "- SDK health: `GET http://127.0.0.1:8768/health` returns OK.",
        "- Legacy endpoint smoke through gateway:",
        "  - `GET /install.ps1`",
        "  - `GET /sdk/docker-compose.yml`",
        "  - `POST /v1/auth/token` with known test key or expected 401 shape",
        "  - `GET /chat/{agent_id}` for a test agent",
        "- SDK endpoint smoke through gateway:",
        "  - two-tenant HTTP E2E",
        "  - cross-tenant GET returns `404`",
        "  - tenant injection returns `400`",
        "  - fuzz `--iterations 100` returns no residue",
        "- NEXUS signs the cutover plan.",
        "",
        "## Rollback",
        "",
        "Rollback must be one command path and must restore the old public API:",
        "",
        "```bash",
        "systemctl --user stop seal-memory-sdk-gateway.service",
        "docker start soul-api-server",
        "curl -s http://127.0.0.1:8767/health",
        "```",
        "",
        "If gateway cutover is done by changing container port mapping instead of a host gateway, rollback is:",
        "",
        "```bash",
        "docker rm -f soul-api-server-gateway",
        "docker start soul-api-server",
        "curl -s http://127.0.0.1:8767/health",
        "```",
        "",
        "No DNS/client SDK default should be changed until rollback has been tested.",
        "",
        "## Current State",
        "",
        "- `:8767`: old Docker container `soul-api-server`; public legacy SOUL API.",
        "- `:8768`: user systemd service `seal-memory-sdk-api.service`; tenant-safe SDK Memory API staging.",
        "- `:8780`: user systemd service `seal-memory-sdk-gateway.service`; gateway staging.",
        "- Direct replacement remains blocked because SDK API does not implement legacy soul/auth/widget/installer endpoints.",
        "",
        "## Next Implementation Unit",
        "",
        "After staging gateway tests pass, prepare the real cutover plan that moves legacy behind a fallback port and binds the gateway to public `:8767`. That step is not automatic and requires explicit William approval because it changes public service routing.",
        "",
    ]
    return "\n".join(md)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-url", default=LEGACY_DEFAULT)
    parser.add_argument("--sdk-url", default=SDK_DEFAULT)
    parser.add_argument("--json-out", default="memory/diagnostic/results/soul_memory_sdk_cutover_audit_latest.json")
    parser.add_argument("--md-out", default="memory/diagnostic/soul_memory_sdk_cutover_plan.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = compare(args.legacy_url, args.sdk_url)
    json_out = Path(args.json_out)
    md_out = Path(args.md_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    md_out.write_text(render_markdown(data))
    print(json.dumps(data, indent=2, sort_keys=True))
    print(f"json_out={json_out}")
    print(f"md_out={md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
