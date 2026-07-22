from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "ops/nginx/api.soulsmemory.com.conf.template"
RENDER_PATH = ROOT / "ops/render_api_soulsmemory_nginx.py"


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_api_soulsmemory_nginx", RENDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(tmp_path: Path) -> str:
    module = _load_renderer()
    return module.render_config(
        TEMPLATE_PATH.read_text(encoding="utf-8"),
        certificate=tmp_path / "tls.crt",
        certificate_key=tmp_path / "tls.key",
        access_log=tmp_path / "access.log",
        error_log=tmp_path / "error.log",
    )


def test_edge_is_isolated_tls_only_and_loopback_origin(tmp_path: Path) -> None:
    config = _render(tmp_path)
    assert "server_name api.soulsmemory.com;" in config
    assert "server_name soulsmemory.com" not in config
    assert "listen 443 ssl http2;" in config
    assert "return 308 https://api.soulsmemory.com$request_uri;" in config
    assert "server 127.0.0.1:8780" in config
    assert "172.22.0.1:8767" not in config
    assert "127.0.0.1:8768" not in config


def test_edge_has_fail_closed_surface_limits_and_headers(tmp_path: Path) -> None:
    config = _render(tmp_path)
    for route in (
        "/health",
        "/openapi.json",
        "/v1/memories",
        "/v1/recall",
        "/v1/tenant/memories",
        "/v1/tenant/recall",
    ):
        assert f"location = {route}" in config
    assert "location ~ ^/v1/memories/[0-9]+$" in config
    assert "location / {\n        return 404;" in config
    assert "/v1/admin" not in config
    assert "location = /v1/memory" not in config
    assert "client_max_body_size 1m;" in config
    assert "limit_req zone=soul_sdk_edge_aggregate" in config
    assert "limit_req zone=soul_sdk_edge_per_peer" in config
    assert "limit_req_status 429;" in config
    assert "add_header X-Request-ID $request_id always;" in config
    assert "Strict-Transport-Security" in config
    assert "Content-Security-Policy" in config


def test_edge_never_trusts_client_forwarding_headers(tmp_path: Path) -> None:
    config = _render(tmp_path)
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert "proxy_set_header X-Real-IP $remote_addr;" in config
    assert "$proxy_add_x_forwarded_for" not in config
    assert "$http_x_forwarded_for" not in config
    assert "real_ip_header" not in config


def test_renderer_rejects_unsafe_or_unresolved_paths(tmp_path: Path) -> None:
    module = _load_renderer()
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="absolute"):
        module.render_config(
            template,
            certificate=Path("relative.crt"),
            certificate_key=tmp_path / "key",
            access_log=tmp_path / "access",
            error_log=tmp_path / "error",
        )
    with pytest.raises(ValueError, match="unsafe"):
        module.render_config(
            template,
            certificate=Path("/tmp/bad;directive"),
            certificate_key=tmp_path / "key",
            access_log=tmp_path / "access",
            error_log=tmp_path / "error",
        )
    with pytest.raises(ValueError, match="unsafe"):
        module.render_config(
            template,
            certificate=Path('/tmp/bad"directive'),
            certificate_key=tmp_path / "key",
            access_log=tmp_path / "access",
            error_log=tmp_path / "error",
        )
    with pytest.raises(ValueError, match="unsafe"):
        module.render_config(
            template,
            certificate=Path("/tmp/bad path"),
            certificate_key=tmp_path / "key",
            access_log=tmp_path / "access",
            error_log=tmp_path / "error",
        )


def test_rendered_config_passes_real_nginx_t(tmp_path: Path) -> None:
    nginx = shutil.which("nginx") or "/usr/sbin/nginx"
    openssl = shutil.which("openssl")
    if not Path(nginx).is_file() or openssl is None:
        pytest.skip("nginx/openssl unavailable")

    cert = tmp_path / "tls.crt"
    key = tmp_path / "tls.key"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=api.soulsmemory.com",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    module = _load_renderer()
    rendered = module.render_config(
        TEMPLATE_PATH.read_text(encoding="utf-8"),
        certificate=cert,
        certificate_key=key,
        access_log=tmp_path / "access.log",
        error_log=tmp_path / "error.log",
    )
    # Debian's nginx -t opens listen sockets. Use unprivileged ports in this
    # isolated syntax test; the structural test above pins production to 80/443.
    rendered = rendered.replace("listen 443 ssl http2;", "listen 18443 ssl http2;")
    rendered = rendered.replace("listen [::]:443 ssl http2;", "listen [::]:18443 ssl http2;")
    rendered = rendered.replace("listen 80;", "listen 18080;")
    rendered = rendered.replace("listen [::]:80;", "listen [::]:18080;")
    rendered_path = tmp_path / "api.conf"
    rendered_path.write_text(rendered, encoding="utf-8")
    root_config = tmp_path / "nginx.conf"
    root_config.write_text(
        "\n".join(
            [
                "worker_processes 1;",
                f"pid {tmp_path / 'nginx.pid'};",
                f"error_log {tmp_path / 'root-error.log'};",
                "events { worker_connections 64; }",
                "http {",
                "    default_type application/octet-stream;",
                "    access_log off;",
                f"    include {rendered_path};",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [nginx, "-t", "-p", str(tmp_path), "-c", str(root_config)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test is successful" in result.stderr
