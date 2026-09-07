"""Contrato de descargas automáticas visible en Panel SOUL."""

import importlib.util
import time
import urllib.error
from pathlib import Path


DASHBOARD = (
    Path(__file__).resolve().parents[1]
    / "fable"
    / "soul_standalone"
    / "spectre_dashboard.py"
)
LATEST_RELEASES = {
    "core": "https://github.com/sknaider/soul-framework/releases/latest",
    "platform": "https://github.com/sknaider/soul-platform/releases/latest",
}


def _module():
    spec = importlib.util.spec_from_file_location("spectre_dashboard_release_test", DASHBOARD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_panel_exposes_both_installers_with_latest_fallbacks() -> None:
    page = _module().PAGE
    assert "Core básico · Descargar" in page
    assert "Platform completo · Descargar" in page
    assert 'id=coreInstaller' in page
    assert 'id=platformInstaller' in page
    for latest in LATEST_RELEASES.values():
        assert f'href="{latest}"' in page
    assert "target=_blank rel=noopener" in page


def test_installer_link_is_version_independent() -> None:
    """Fallback links must follow latest rather than freezing a release tag."""
    page = _module().PAGE
    assert "proyecto-seal-2026" not in page
    assert "soul-platform/releases/tag/v" not in page
    assert "soul-platform/releases/download/v" not in page
    assert "soul-framework/releases/tag/v" not in page
    assert "soul-framework/releases/download/v" not in page


def test_release_metadata_selects_only_the_expected_wheel() -> None:
    module = _module()
    payload = {
        "tag_name": "v9.8.7",
        "html_url": "https://github.com/sknaider/soul-framework/releases/tag/v9.8.7",
        "assets": [
            {
                "name": "soul_framework-9.8.7.tar.gz",
                "browser_download_url": "https://github.com/sknaider/soul-framework/releases/download/v9.8.7/soul_framework-9.8.7.tar.gz",
            },
            {
                "name": "soul_framework-9.8.7-py3-none-any.whl",
                "browser_download_url": "https://github.com/sknaider/soul-framework/releases/download/v9.8.7/soul_framework-9.8.7-py3-none-any.whl",
            },
        ],
    }
    result = module._release_metadata("core", payload)
    assert result["direct"] is True
    assert result["version"] == "v9.8.7"
    assert result["installer_url"].endswith("soul_framework-9.8.7-py3-none-any.whl")


def test_release_metadata_rejects_an_untrusted_download_host() -> None:
    module = _module()
    payload = {
        "tag_name": "v9.8.7",
        "html_url": "https://github.com/sknaider/soul-platform/releases/tag/v9.8.7",
        "assets": [
            {
                "name": "SOUL-Platform-9.8.7-Windows.zip",
                "browser_download_url": "https://evil.example/SOUL-Platform-9.8.7-Windows.zip",
            }
        ],
    }
    result = module._release_metadata("platform", payload)
    assert result["direct"] is False
    assert result["installer_url"] == LATEST_RELEASES["platform"]


def test_platform_metadata_selects_public_windows_bundle() -> None:
    module = _module()
    payload = {
        "tag_name": "v0.5.6",
        "html_url": "https://github.com/sknaider/soul-platform/releases/tag/v0.5.6",
        "assets": [{
            "name": "SOUL-Platform-0.5.6-Windows.zip",
            "browser_download_url": "https://github.com/sknaider/soul-platform/releases/download/v0.5.6/SOUL-Platform-0.5.6-Windows.zip",
        }],
    }
    result = module._release_metadata("platform", payload)
    assert result["direct"] is True
    assert result["version"] == "v0.5.6"
    assert result["installer_url"].endswith("SOUL-Platform-0.5.6-Windows.zip")
    assert result["asset_name"] == "SOUL-Platform-0.5.6-Windows.zip"
    assert result["_asset_api_path"] == ""


def test_release_metadata_rejects_wrong_tag_userinfo_and_query() -> None:
    module = _module()
    base = {
        "tag_name": "v9.8.7",
        "html_url": "https://github.com/sknaider/soul-framework/releases/tag/v9.8.7",
        "assets": [{
            "name": "soul_framework-9.8.7-py3-none-any.whl",
            "browser_download_url": "https://github.com/sknaider/soul-framework/releases/download/v0.0.1/evil-soul_framework-9.8.7-py3-none-any.whl",
        }],
    }
    assert module._release_metadata("core", base)["direct"] is False
    base["assets"][0]["browser_download_url"] = "https://secret@github.com/sknaider/soul-framework/releases/download/v9.8.7/soul_framework-9.8.7-py3-none-any.whl"
    assert module._release_metadata("core", base)["direct"] is False
    base["assets"][0]["browser_download_url"] = "https://github.com/sknaider/soul-framework/releases/download/v9.8.7/soul_framework-9.8.7-py3-none-any.whl?raw=1"
    assert module._release_metadata("core", base)["direct"] is False


def test_malformed_platform_payload_fails_closed() -> None:
    module = _module()
    result = module._release_metadata("platform", {"not": "a-release"})
    assert result["direct"] is False
    assert result["installer_url"] == LATEST_RELEASES["platform"]


def test_release_metadata_rejects_prereleases() -> None:
    module = _module()
    payload = {
        "tag_name": "v9.8.7",
        "html_url": "https://github.com/sknaider/soul-framework/releases/tag/v9.8.7",
        "prerelease": True,
        "assets": [{
            "name": "soul_framework-9.8.7-py3-none-any.whl",
            "browser_download_url": "https://github.com/sknaider/soul-framework/releases/download/v9.8.7/soul_framework-9.8.7-py3-none-any.whl",
        }],
    }
    result = module._release_metadata("core", payload)
    assert result["direct"] is False
    assert result["installer_url"] == LATEST_RELEASES["core"]


def test_refresh_failure_keeps_last_known_good(monkeypatch) -> None:
    module = _module()
    known = {
        "product": "core",
        "label": "Core básico",
        "version": "v1.2.3",
        "installer_url": "https://github.com/sknaider/soul-framework/releases/download/v1.2.3/soul_framework-1.2.3-py3-none-any.whl",
        "release_url": "https://github.com/sknaider/soul-framework/releases/tag/v1.2.3",
        "direct": True,
    }
    module._release_cache["core"] = (time.monotonic() - module.RELEASE_CACHE_TTL - 1, known)

    def unavailable(*_args, **_kwargs):
        raise urllib.error.URLError("rate limited")

    monkeypatch.setattr(module.urllib.request, "urlopen", unavailable)
    assert module.latest_release("core") == known
