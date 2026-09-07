"""Regresiones de la frontera MCP nativa sin lanzar un navegador real."""

from __future__ import annotations

import json
import socket
import stat
from pathlib import Path

import pytest

import seal_cdp_mcp as server
from mcp_web_soul_control import BrowserControlPlane, REMOTE_COMMIT
from mcp_web_soul_security import AuditTrail
from mcp_web_soul_profiles import ProfileVault
from seal_cdp import CDP


class FakeBrowser:
    def __init__(self) -> None:
        self.typed = None
        self.cookie = None
        self.saved = None
        self.shot = None
        self.clicked = None
        self.actions = []
        self.current_url = "about:blank"
        self.element_context = {
            "found": True,
            "tag": "BUTTON",
            "type": "button",
            "role": "",
            "ariaLabel": "",
            "text": "Abrir detalles",
            "href": "",
            "formAction": "",
            "formMethod": "",
            "submitsForm": False,
        }

    def eval_js(self, expression):
        if expression == "location.href":
            return self.current_url
        if expression == "document.title":
            return "Test page"
        assert "document.querySelector" in expression
        return dict(self.element_context)

    def click(self, selector):
        self.clicked = selector

    def close(self):
        self.actions.append(("close",))

    def hover(self, selector): self.actions.append(("hover", selector))
    def drag(self, source, target): self.actions.append(("drag", source, target))
    def select_option(self, selector, value): self.actions.append(("select", selector, value))
    def press_key(self, key): self.actions.append(("key", key))
    def upload_file(self, selector, path): self.actions.append(("upload", selector, path))
    def handle_dialog(self, accept, prompt_text=""): self.actions.append(("dialog", accept, prompt_text))
    def navigate_back(self): self.actions.append(("back",)); return True
    def navigate(self, url, **kwargs):
        self.current_url = url
        self.actions.append(("navigate", url, kwargs))
    def open_tab(self, url): self.actions.append(("open_tab", url)); return "tab-new"
    def switch_tab(self, target): self.actions.append(("switch_tab", target))
    def emulate_device(self, width, height): self.actions.append(("resize", width, height))

    def type_text(self, selector, value):
        self.typed = (selector, value)

    def set_cookie(self, name, value, *, url):
        self.cookie = (name, value, url)
        return True

    def network_requests(self, *, with_headers=False):
        if not with_headers:
            return [{"url": self.current_url, "status": 200}]
        return [{
            "url": "https://example.test/x?token=query-secret",
            "status": 200,
            "req_headers": {"Authorization": "Bearer header-secret", "Accept": "text/html"},
            "resp_headers": {"Set-Cookie": "sid=response-secret", "Content-Type": "text/html"},
            "sent_cookies": [{"name": "sid", "value": "cookie-secret"}],
        }]

    def save_url(self, url, path):
        self.saved = (url, path)
        Path(path).write_bytes(b"ok")
        return {"status": 200, "bytes": 2, "path": path}

    def screenshot(self, path, *, full_page=False):
        self.shot = (path, full_page)
        Path(path).write_bytes(b"png")

    def text(self):
        raise RuntimeError("token=runtime-secret")

    def tabs(self):
        return [{"id": "one", "url": "https://example.test/a?token=tab-secret", "title": "A"}]

    def console(self):
        return [{"level": "error", "text": "token=console-secret"}]


@pytest.fixture()
def isolated_server(monkeypatch, tmp_path):
    browser = FakeBrowser()
    audit = AuditTrail(tmp_path / "audit" / "events.jsonl", agent="ADA")
    control = BrowserControlPlane(tmp_path / "control.sqlite3")
    monkeypatch.setattr(server, "_get", lambda: browser)
    monkeypatch.setattr(server, "_AUDIT", audit)
    monkeypatch.setattr(server, "_CONTROL", control)
    monkeypatch.setattr(server, "_SESSION_ID", None)
    monkeypatch.setattr(server, "_ARTIFACT_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(server, "_browser", None)
    monkeypatch.setattr(server, "_VISUAL", None)
    monkeypatch.setattr(server, "_PROFILE_DIR", None)
    monkeypatch.setattr(server, "_VAULT", None)
    return browser, audit, control, tmp_path


def _audit_text(audit: AuditTrail) -> str:
    return audit.path.read_text(encoding="utf-8")


def test_typed_text_and_cookie_never_enter_audit(isolated_server):
    browser, audit, control, _ = isolated_server
    typed_secret = "typed-password-secret"
    cookie_secret = "cookie-value-secret"

    assert server.type_text("#notes", typed_secret)["ok"] is True
    session_id = server._session_id()
    args = {"name": "sid", "value": cookie_secret, "url": "https://example.test/?token=q"}
    approval = control.request_approval(session_id=session_id, tool="set_cookie", arguments=args)
    control.approve(approval["approval_id"], operator="William")
    assert server.set_cookie(**args, approval_id=approval["approval_id"])["ok"] is True

    raw = _audit_text(audit)
    assert typed_secret not in raw
    assert cookie_secret not in raw
    assert "?token=q" not in raw
    assert browser.typed == ("#notes", typed_secret)
    assert browser.cookie[1] == cookie_secret
    assert stat.S_IMODE(audit.path.stat().st_mode) == 0o600
    assert audit.verify() == (True, 4)


def test_network_response_is_redacted_before_return(isolated_server):
    _, audit, _, _ = isolated_server
    result = server.network()
    encoded = json.dumps(result)

    assert result["count"] == 1
    assert "header-secret" not in encoded
    assert "response-secret" not in encoded
    assert "cookie-secret" not in encoded
    assert "query-secret" not in encoded
    assert result["requests"][0]["url"] == "https://example.test/x"
    assert result["requests"][0]["req_headers"] == {"Accept": "text/html"}
    assert result["requests"][0]["resp_headers"] == {"Content-Type": "text/html"}
    assert result["requests"][0]["sent_cookies"] == {"redacted": True, "count": 1}
    assert audit.verify() == (True, 2)


def test_artifacts_are_confined_to_managed_root(isolated_server):
    browser, audit, control, root = isolated_server

    good = server.screenshot("screens/page.png", full_page=True)
    assert good["ok"] is True
    assert Path(good["path"]).is_relative_to(root / "artifacts")
    assert browser.shot[1] is True

    args = {"url": "https://example.test/file", "path": "../../escape.bin"}
    requested = control.request_approval(
        session_id=server._session_id(), tool="save_url", arguments=args,
    )
    control.approve(requested["approval_id"], operator="William")
    bad = server.save_url(**args, approval_id=requested["approval_id"])
    assert bad["ok"] is False
    assert not (root.parent / "escape.bin").exists()
    assert audit.verify() == (True, 4)


def test_remote_click_effect_then_failure_stays_indeterminate(isolated_server):
    browser, _audit, control, _ = isolated_server
    browser.element_context["submitsForm"] = True
    side_effects: list[str] = []

    def commit_then_fail(selector: str) -> None:
        side_effects.append(selector)
        raise TimeoutError("response timed out after remote commit")

    browser.click = commit_then_fail
    args = {"selector": "#opaque"}
    approval = control.request_approval(
        session_id=server._session_id(),
        tool="click",
        arguments=args,
        risk_context=browser.element_context,
    )
    control.approve(approval["approval_id"], operator="William")

    result = server.click(**args, approval_id=approval["approval_id"])
    assert result["ok"] is False
    assert side_effects == ["#opaque"]
    assert control.remote_effect_operation(approval["approval_id"])["status"] == "indeterminate"
    with pytest.raises(Exception) as blocked:
        control.request_approval(
            session_id=server._session_id(),
            tool="click",
            arguments=args,
            risk_context=browser.element_context,
        )
    assert getattr(blocked.value, "code", None) == "remote_effect_reconciliation_required"


def test_string_error_is_audited_as_failure_without_secret(isolated_server):
    _, audit, _, _ = isolated_server
    result = server.get_text()
    assert result.startswith("[error:")

    record = json.loads(audit.path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["ok"] is False
    assert "runtime-secret" not in json.dumps(record)
    assert "token=[redacted]" in record["error"]


def test_grouped_observe_sanitizes_tabs_and_console(isolated_server):
    _, audit, _, _ = isolated_server
    tabs = server.browser_observe("tabs")
    console = server.browser_observe("console")
    encoded = json.dumps({"tabs": tabs, "console": console})
    assert "tab-secret" not in encoded
    assert tabs["tabs"][0]["url"] == "https://example.test/a"
    assert "console-secret" not in encoded
    assert "token=[redacted]" in encoded
    assert audit.verify()[0] is True


def test_private_and_link_local_destinations_are_denied(monkeypatch):
    assert CDP._host_is_public("127.0.0.1") is False
    assert CDP._host_is_public("10.1.2.3") is False
    assert CDP._host_is_public("169.254.169.254") is False
    assert CDP._host_is_public("localhost") is False
    assert CDP._host_is_public("1.1.1.1") is True

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ],
    )
    assert CDP._host_is_public("mixed.example") is False


def test_fetch_gate_delegates_domain_dns_only_to_pinned_proxy(monkeypatch):
    cdp = CDP.__new__(CDP)
    cdp._events = []
    cdp._allowed_origins = None
    cdp._allowed_methods = None
    cdp._deny_private_networks = True
    cdp._egress_proxy = object()
    sent = []
    cdp._send = lambda method, params=None: sent.append((method, params)) or {}
    cdp._audit = lambda *args, **kwargs: None
    monkeypatch.setattr(
        CDP, "_host_is_public",
        staticmethod(lambda _host: (_ for _ in ()).throw(AssertionError("double DNS"))),
    )

    cdp._handle_event({
        "method": "Fetch.requestPaused",
        "params": {
            "requestId": "public",
            "request": {"url": "https://example.com/", "method": "GET"},
        },
    })
    cdp._handle_event({
        "method": "Fetch.requestPaused",
        "params": {
            "requestId": "private",
            "request": {"url": "http://127.0.0.1/", "method": "GET"},
        },
    })

    assert sent[0] == ("Fetch.continueRequest", {"requestId": "public"})
    assert sent[1] == (
        "Fetch.failRequest", {"requestId": "private", "errorReason": "BlockedByClient"},
    )


def test_default_runtime_is_headless_without_human_broker(monkeypatch, tmp_path):
    captured = {}

    class FakeCDP:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    monkeypatch.delenv("MCP_WEB_SOUL_HUMAN_CREDENTIAL_BROKER", raising=False)
    monkeypatch.setattr(server, "CDP", FakeCDP)
    monkeypatch.setattr(server, "_browser", None)
    monkeypatch.setattr(server, "_VISUAL", None)
    monkeypatch.setattr(server, "_PROFILE_DIR", None)
    monkeypatch.setattr(server, "_STATE_ROOT", tmp_path)
    monkeypatch.setattr(server, "_CDP_AUDIT_PATH", tmp_path / "audit" / "cdp.jsonl")

    assert isinstance(server._get(), FakeCDP)
    assert captured["headless"] is True
    assert captured["process_env"] is None
    assert server._VISUAL is None


def test_forged_environment_cannot_enable_same_uid_visual(monkeypatch, tmp_path):
    captured = {}

    class FakeCDP:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    monkeypatch.setenv("MCP_WEB_SOUL_HUMAN_CREDENTIAL_BROKER", "operator-v1")
    monkeypatch.setattr(server, "CDP", FakeCDP)
    monkeypatch.setattr(server, "_browser", None)
    monkeypatch.setattr(server, "_VISUAL", None)
    monkeypatch.setattr(server, "_PROFILE_DIR", None)
    monkeypatch.setattr(server, "_STATE_ROOT", tmp_path)
    monkeypatch.setattr(server, "_CDP_AUDIT_PATH", tmp_path / "audit" / "cdp.jsonl")

    assert server._human_credential_broker_enabled() is False
    assert isinstance(server._get(), FakeCDP)
    assert captured["headless"] is True
    assert captured["process_env"] is None


def test_takeover_fails_closed_before_viewer_without_operator_broker(
    monkeypatch, isolated_server,
):
    monkeypatch.delenv("MCP_WEB_SOUL_HUMAN_CREDENTIAL_BROKER", raising=False)
    monkeypatch.setattr(
        server, "_visual", lambda: (_ for _ in ()).throw(AssertionError("viewer started")),
    )
    result = server.browser_takeover("request")
    assert result["ok"] is False
    assert result["error_code"] == "human_credential_delivery_unavailable"


def test_opaque_submit_button_requires_approval_from_dom_context(isolated_server):
    browser, audit, control, _ = isolated_server
    browser.element_context.update(
        {
            "type": "submit",
            "text": "Continue",
            "formAction": "https://example.test/orders",
            "formMethod": "post",
            "submitsForm": True,
        }
    )

    denied = server.click("#x7")
    assert denied["ok"] is False
    assert denied["error_code"] == "approval_required"
    assert browser.clicked is None

    args = {"selector": "#x7"}
    risk_context = server._risk_context("click", args)
    requested = control.request_approval(
        session_id=server._session_id(),
        tool="click",
        arguments=args,
        risk_context=risk_context,
    )
    assert requested["action_class"] == REMOTE_COMMIT
    control.approve(requested["approval_id"], operator="William")
    result = server.click("#x7", approval_id=requested["approval_id"])
    assert result["ok"] is True
    assert browser.clicked == "#x7"
    assert audit.verify()[0] is True


def test_failed_dom_inspection_fails_closed_as_sensitive(isolated_server, monkeypatch):
    browser, _, _, _ = isolated_server
    monkeypatch.setattr(browser, "eval_js", lambda expression: (_ for _ in ()).throw(RuntimeError("boom")))

    result = server.click("#opaque")

    assert result["ok"] is False
    assert result["error_code"] == "approval_required"
    assert browser.clicked is None


def test_password_field_is_detected_from_dom_not_selector_text(isolated_server):
    browser, _, control, _ = isolated_server
    browser.element_context.update({"type": "password", "autocomplete": "current-password"})

    denied = server.type_text("#x7", "secret")
    assert denied["ok"] is False
    assert denied["error_code"] == "approval_required"
    assert browser.typed is None

    args = {"selector": "#x7", "value": "secret"}
    risk_context = server._risk_context("type_text", args)
    requested = control.request_approval(
        session_id=server._session_id(), tool="type_text", arguments=args,
        risk_context=risk_context,
    )
    control.approve(requested["approval_id"], operator="William")
    assert server.type_text("#x7", "secret", approval_id=requested["approval_id"])["ok"] is True
    assert browser.typed == ("#x7", "secret")


def test_grouped_press_key_target_enter_requires_approval(isolated_server):
    browser, _, _, _ = isolated_server

    denied = server.browser_action("press_key", target="Enter")

    assert denied["ok"] is False
    assert denied["error_code"] == "approval_required"
    assert ("key", "Enter") not in browser.actions


@pytest.mark.parametrize(
    ("invoke", "forbidden_action"),
    [
        (lambda: server.browse("https://example.test/eliminar-cuenta"), "navigate"),
        (
            lambda: server.browser_action(
                "navigate", url="https://example.test/confirm-payment"
            ),
            "navigate",
        ),
        (
            lambda: server.browser_action(
                "open_tab", url="https://example.test/cerrar-sesion"
            ),
            "open_tab",
        ),
    ],
)
def test_sensitive_navigation_without_approval_never_reaches_cdp(
    isolated_server, invoke, forbidden_action,
):
    browser, _, _, _ = isolated_server

    denied = invoke()

    assert denied["ok"] is False
    assert denied["error_code"] == "approval_required"
    assert not any(action[0] == forbidden_action for action in browser.actions)


def test_sensitive_save_url_without_approval_never_reaches_cdp(isolated_server):
    browser, _, _, _ = isolated_server

    denied = server.save_url(
        "https://example.test/confirm-payment", "downloads/receipt.bin"
    )

    assert denied["ok"] is False
    assert denied["error_code"] == "approval_required"
    assert browser.saved is None


def test_sensitive_direct_browse_can_execute_only_with_exact_approval(isolated_server):
    browser, audit, control, _ = isolated_server
    browser.text = lambda: "safe page"
    url = "https://example.test/eliminar-cuenta"
    args = {"url": url}
    requested = control.request_approval(
        session_id=server._session_id(), tool="browse", arguments=args,
    )
    control.approve(requested["approval_id"], operator="William")

    result = server.browse(url, approval_id=requested["approval_id"])

    assert result["ok"] is True
    assert ("navigate", url, {"wait_until": "load"}) in browser.actions
    assert control.remote_effect_operation(requested["approval_id"])["status"] == "completed"
    assert audit.verify()[0] is True


def test_sensitive_save_url_with_exact_approval_is_journaled(isolated_server):
    browser, audit, control, _ = isolated_server
    url = "https://example.test/confirm-payment"
    args = {"url": url, "path": "downloads/receipt.bin"}
    requested = control.request_approval(
        session_id=server._session_id(), tool="save_url", arguments=args,
    )
    control.approve(requested["approval_id"], operator="William")

    result = server.save_url(
        url, "downloads/receipt.bin", approval_id=requested["approval_id"]
    )

    assert result["ok"] is True
    assert browser.saved is not None and browser.saved[0] == url
    assert control.remote_effect_operation(requested["approval_id"])["status"] == "completed"
    assert audit.verify()[0] is True


@pytest.mark.parametrize(
    ("action", "kwargs", "forbidden_action"),
    [
        (" press_key ", {"target": "Enter"}, "key"),
        (
            " navigate ",
            {"url": "https://example.test/eliminar-cuenta"},
            "navigate",
        ),
        (
            " set_cookie ",
            {"target": "sid", "value": "secret", "url": "https://example.test"},
            "set_cookie",
        ),
    ],
)
def test_whitespace_cannot_turn_grouped_sensitive_action_into_unapproved_cdp_call(
    isolated_server, action, kwargs, forbidden_action,
):
    browser, _, _, _ = isolated_server

    denied = server.browser_action(action, **kwargs)

    assert denied["ok"] is False
    assert denied["error_code"] == "approval_required"
    if forbidden_action == "set_cookie":
        assert browser.cookie is None
    else:
        assert not any(item[0] == forbidden_action for item in browser.actions)


def test_grouped_actions_cover_native_playwright_parity(isolated_server):
    browser, _, control, root = isolated_server
    upload = root / "artifacts" / "files" / "sample.txt"
    upload.parent.mkdir(parents=True)
    upload.write_text("ok", encoding="utf-8")

    assert server.browser_action("hover", target="#a")["ok"] is True
    assert server.browser_action("drag", target="#a", value="#b")["ok"] is True
    assert server.browser_action("select_option", target="#country", value="PE")["ok"] is True
    assert server.browser_action("back")["ok"] is True
    assert server.browser_action("dialog_dismiss")["ok"] is True
    assert server.browser_action("switch_tab", target="tab-2")["ok"] is True
    assert server.browser_action("resize", target="1280x720")["ok"] is True

    for action, target, value in (
        ("press_key", "", "Enter"),
        ("upload_file", "input[type=file]", "files/sample.txt"),
        ("dialog_accept", "", "yes"),
    ):
        args = {"action": action, "target": target, "value": value}
        requested = control.request_approval(
            session_id=server._session_id(), tool="browser_action", arguments=args
        )
        control.approve(requested["approval_id"], operator="William")
        assert server.browser_action(
            action, target=target, value=value, approval_id=requested["approval_id"]
        )["ok"] is True

    assert ("hover", "#a") in browser.actions
    assert ("drag", "#a", "#b") in browser.actions
    assert ("resize", 1280, 720) in browser.actions
    assert ("dialog", False, "") in browser.actions
    assert ("switch_tab", "tab-2") in browser.actions


def test_close_browser_is_local_mutation_without_external_approval(isolated_server):
    _, audit, _, _ = isolated_server

    assert server.close_browser()["ok"] is True
    assert audit.verify()[0] is True


def test_browser_profile_save_requires_approval_and_encrypts(isolated_server, monkeypatch):
    browser, _, control, root = isolated_server
    profile = root / "runtime-profile"
    profile.mkdir()
    (profile / "Cookies").write_text("session-secret", encoding="utf-8")
    vault = ProfileVault(root / "vault")
    monkeypatch.setattr(server, "_browser", browser)
    monkeypatch.setattr(server, "_PROFILE_DIR", str(profile))
    monkeypatch.setattr(server, "_VAULT", vault)

    denied = server.browser_profile("save", "william")
    assert denied["error_code"] == "approval_required"
    assert profile.exists()

    args = {"operation": "save", "name": "william"}
    requested = control.request_approval(
        session_id=server._session_id(), tool="browser_profile", arguments=args
    )
    control.approve(requested["approval_id"], operator="William")
    saved = server.browser_profile("save", "william", requested["approval_id"])
    assert saved["ok"] is True
    assert saved["state"] == "encrypted_at_rest"
    assert not profile.exists()
    encrypted = vault._path("william").read_bytes()
    assert b"session-secret" not in encrypted
