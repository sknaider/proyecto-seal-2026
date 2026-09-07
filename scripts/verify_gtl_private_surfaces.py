#!/usr/bin/env python3
"""Canary externo para las superficies privadas ADA Chat y OpenClaw de GTL."""

from __future__ import annotations

import argparse
import json
import socket
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _request(opener, url: str, *, cookie: str | None = None) -> dict[str, object]:
    headers = {"User-Agent": "SEAL-GTL-private-surface-canary/1"}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(request, timeout=10) as response:
            body = response.read()
            return {
                "status": response.status,
                "location": response.headers.get("Location", ""),
                "bytes": len(body),
            }
    except urllib.error.HTTPError as error:
        body = error.read()
        return {
            "status": error.code,
            "location": error.headers.get("Location", ""),
            "bytes": len(body),
        }


def _location_path(value: object) -> str:
    return urllib.parse.urlsplit(str(value)).path + (
        "?" + urllib.parse.urlsplit(str(value)).query
        if urllib.parse.urlsplit(str(value)).query
        else ""
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://gtl.pe")
    parser.add_argument("--gateway-host", default="72.60.126.13")
    parser.add_argument("--gateway-port", type=int, default=18789)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    opener = urllib.request.build_opener(_NoRedirect)
    observations = {
        "ada_legacy": _request(opener, f"{base}/ada/"),
        "ada_page": _request(opener, f"{base}/sistema/ada/"),
        "ada_index": _request(opener, f"{base}/sistema/ada/index.html"),
        "ada_double_slash": _request(opener, f"{base}/sistema/ada//"),
        "ada_encoded_slash": _request(opener, f"{base}/sistema/ada%2F"),
        "ada_path_double": _request(opener, f"{base}/sistema//ada/"),
        "ada_api": _request(opener, f"{base}/sistema/ada/api/"),
        "ada_api_child": _request(opener, f"{base}/sistema/ada/api/test"),
        "ada_api_no_slash": _request(opener, f"{base}/sistema/ada/api"),
        "ada_fake_session": _request(
            opener,
            f"{base}/sistema/ada/",
            cookie="session=definitely-invalid",
        ),
        "openclaw_page": _request(opener, f"{base}/sistema/openclaw/"),
        "login": _request(opener, f"{base}/sistema/login"),
        "system": _request(opener, f"{base}/sistema/"),
    }

    direct_gateway_blocked = False
    with socket.socket() as probe:
        probe.settimeout(3)
        try:
            probe.connect((args.gateway_host, args.gateway_port))
        except OSError:
            direct_gateway_blocked = True
    observations["direct_gateway"] = {"blocked": direct_gateway_blocked}

    expected_redirects = {
        "ada_page": "/sistema/login?redirect=/sistema/ada/",
        "ada_index": "/sistema/login?redirect=/sistema/ada/",
        "ada_double_slash": "/sistema/login?redirect=/sistema/ada/",
        "ada_encoded_slash": "/sistema/login?redirect=/sistema/ada/",
        "ada_path_double": "/sistema/login?redirect=/sistema/ada/",
        "ada_api": "/sistema/login?redirect=/sistema/ada/",
        "ada_api_child": "/sistema/login?redirect=/sistema/ada/",
        "ada_fake_session": "/sistema/login?redirect=/sistema/ada/",
        "openclaw_page": "/sistema/login?redirect=/sistema/openclaw/",
    }
    failures: list[str] = []
    if observations["ada_legacy"]["status"] != 301:
        failures.append("ada_legacy_not_canonical_redirect")
    if _location_path(observations["ada_legacy"]["location"]) != "/sistema/ada/":
        failures.append("ada_legacy_wrong_target")
    if observations["ada_api_no_slash"]["status"] != 301:
        failures.append("ada_api_no_slash_not_canonical_redirect")
    if _location_path(observations["ada_api_no_slash"]["location"]) != "/sistema/ada/api/":
        failures.append("ada_api_no_slash_wrong_target")
    for name, target in expected_redirects.items():
        if observations[name]["status"] != 302:
            failures.append(f"{name}_not_login_redirect")
        if _location_path(observations[name]["location"]) != target:
            failures.append(f"{name}_wrong_login_target")
    for name in ("login", "system"):
        if observations[name]["status"] != 200:
            failures.append(f"{name}_not_healthy")
    if not direct_gateway_blocked:
        failures.append("gateway_publicly_reachable")

    result = {"ok": not failures, "failures": failures, "observations": observations}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
