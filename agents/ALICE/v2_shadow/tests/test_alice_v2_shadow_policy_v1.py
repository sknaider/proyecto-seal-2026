"""Contrato de la POLÍTICA del asiento «ALICE v2 en sombra»: settings_alice_v2.json y mcp_alice_v2.json.

Revisión independiente de NEXUS (3-sep 00:13): «el sello prueba que no cambiaron; no prueba que su
contenido sea correcto. Alguien afloja un deny mañana y el gate sigue verde». Estos tests afirman la
presencia de los deny que NO pueden faltar y la ausencia de los allow que abren puertas, y que el MCP
del asiento es SOLO el gateway SOUL v2 sin bearer visible. El arnés de mutantes los ejercita sobre
copias mutadas (ALICE_V2_SETTINGS / ALICE_V2_MCP apuntan a la copia).
"""
from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_SETTINGS = HERE.parent / "settings_alice_v2.json"
DEFAULT_MCP = HERE.parent / "mcp_alice_v2.json"


@pytest.fixture(scope="module")
def settings() -> dict:
    return json.loads(pathlib.Path(os.environ.get("ALICE_V2_SETTINGS") or DEFAULT_SETTINGS).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mcp() -> dict:
    return json.loads(pathlib.Path(os.environ.get("ALICE_V2_MCP") or DEFAULT_MCP).read_text(encoding="utf-8"))


def _allow(settings) -> list[str]:
    return list(settings["permissions"].get("allow", []))


def _deny(settings) -> list[str]:
    return list(settings["permissions"].get("deny", []))


# ── settings: lo que NO puede faltar en deny ─────────────────────────────────────────────

DENY_OBLIGATORIOS = [
    # intérpretes: con uno solo, cualquier deny de Bash se rodea
    "Bash(python3:*)", "Bash(python:*)", "Bash(node:*)", "Bash(bash:*)", "Bash(sh:*)",
    # red: canal de exfiltración
    "WebFetch", "WebSearch", "Bash(curl:*)", "Bash(wget:*)", "Bash(nc:*)", "Bash(ssh:*)", "Bash(scp:*)", "Bash(rsync:*)",
    # lectura por shell: salta el deny de Read
    "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(grep:*)", "Bash(find:*)", "Bash(sed:*)", "Bash(awk:*)",
    "Bash(xxd:*)", "Bash(base64:*)", "Bash(env:*)", "Bash(printenv:*)",
    # oráculos de metadata sobre archivos denegados (NEXUS 00:13)
    "Bash(sha256sum:*)", "Bash(stat:*)", "Bash(wc:*)", "Bash(du:*)",
    # destructivos y control de servicios
    "Bash(rm:*)", "Bash(sudo:*)", "Bash(kill:*)", "Bash(pkill:*)", "Bash(docker:*)", "Bash(psql:*)",
    "Bash(systemctl restart:*)", "Bash(systemctl --user restart:*)", "Bash(systemctl --user stop:*)",
    "Bash(git push:*)", "Bash(git reset:*)", "Bash(git checkout:*)", "Bash(git commit:*)",
    # escritura fuera del corral y sobre el estado del CLI
    "Write(//home/dadito/IA/proyecto-seal/**)", "Edit(//home/dadito/IA/proyecto-seal/**)",
    "Write(//home/dadito/.claude/**)", "Edit(//home/dadito/.claude/**)",
    "Write(//home/dadito/.config/**)", "Edit(//home/dadito/.config/**)",
    # secretos e intimidad ajena
    "Read(//home/dadito/.config/**)", "Read(//home/dadito/.claude/**)",
    "Read(//home/dadito/IA/proyecto-seal/messages/.agent_session_token_*)",
    "Read(//home/dadito/IA/proyecto-seal/messages/*_inbox.jsonl)",
    "Read(**/.env)", "Read(**/*.pem)", "Read(**/*.key)",
    # SOUL: sin escritura directa desde el asiento (va por el broker de continuidad ALICE_V2)
    "mcp__soul-v2-gateway__memory_store", "mcp__soul-v2-gateway__consent_grant", "mcp__soul-v2-gateway__system_gateway",
    "mcp__soul-v2-gateway__agent_task", "mcp__soul-v2-gateway__working_state_update", "mcp__soul-v2-gateway__web_search",
]


def test_modo_default_sin_skip(settings):
    assert settings["permissions"].get("defaultMode") == "default"
    assert "bypassPermissions" not in json.dumps(settings)


@pytest.mark.parametrize("regla", DENY_OBLIGATORIOS)
def test_deny_obligatorio_presente(settings, regla):
    assert regla in _deny(settings), f"falta el deny {regla}"


def test_allow_no_abre_puertas(settings):
    allow = _allow(settings)
    prohibidos = ["WebFetch", "WebSearch", "Bash(curl", "Bash(wget", "Bash(cat", "Bash(head", "Bash(tail", "Bash(grep",
                  "Bash(find", "Bash(python", "Bash(bash", "Bash(sh", "Bash(node", "Bash(rm", "Bash(sudo",
                  "Bash(sha256sum", "Bash(stat", "Bash(wc", "Bash(du", "Write", "Edit", "Bash(git push", "Bash(git commit",
                  "mcp__soul-v2-gateway__memory_store", "mcp__soul-v2-gateway__consent_grant", "mcp__soul-v2-gateway__system_gateway"]
    for a in allow:
        assert not any(a.startswith(p) for p in prohibidos), f"allow abre una puerta: {a}"
    assert "Bash" not in allow and "Bash(*)" not in allow, "Bash sin acotar en allow"


# Asimetría allow/deny (NEXUS, re-revisión 00:18): blindar la lista de prohibidos deja abierta la de permitidos.
# Con «deny gana sobre allow», un allow de `perl`/`ruby`/`php` da ejecución arbitraria real porque no está del otro
# lado. Por eso el allow es una LISTA CERRADA: cualquier entrada que no esté acá es una falla, sea la que sea.
ALLOW_CERRADO = {
    "Read", "Grep", "Glob", "Monitor",
    "Bash(ls:*)", "Bash(date:*)",
    "Bash(git status:*)",
    "Bash(systemctl --user is-active:*)", "Bash(systemctl --user status:*)", "Bash(systemctl --user show:*)",
    "Bash(/home/dadito/IA/proyecto-seal/scripts/seal_send.py ALICE-V2:*)",
    "mcp__soul-v2-gateway__boot_context", "mcp__soul-v2-gateway__active_recall",
    "mcp__soul-v2-gateway__memory_hybrid_search", "mcp__soul-v2-gateway__search_code",
    "mcp__soul-v2-gateway__soul_recall_router_tool", "mcp__soul-v2-gateway__soul_snapshot",
    "mcp__soul-v2-gateway__webchat_poll", "mcp__soul-v2-gateway__working_state_get",
    "mcp__soul-v2-gateway__soul_event_log_last", "mcp__soul-v2-gateway__unit_journal_tail",
    "mcp__soul-v2-gateway__working_state_events", "mcp__soul-v2-gateway__capability_scope_summary",
    "mcp__soul-v2-gateway__unit_runtime_status",
}

MCP_READONLY_CERRADO = {
    "mcp__soul-v2-gateway__boot_context",
    "mcp__soul-v2-gateway__active_recall",
    "mcp__soul-v2-gateway__memory_hybrid_search",
    "mcp__soul-v2-gateway__search_code",
    "mcp__soul-v2-gateway__soul_recall_router_tool",
    "mcp__soul-v2-gateway__soul_snapshot",
    "mcp__soul-v2-gateway__webchat_poll",
    "mcp__soul-v2-gateway__working_state_get",
    "mcp__soul-v2-gateway__soul_event_log_last",
    "mcp__soul-v2-gateway__unit_journal_tail",
    "mcp__soul-v2-gateway__working_state_events",
    "mcp__soul-v2-gateway__capability_scope_summary",
    "mcp__soul-v2-gateway__unit_runtime_status",
}


# Grants de escritura 3b (ADA, sujeto 1992962, APPROVE NEXUS #147929; medido 3-sep 23:37 que sin estas
# reglas `dontAsk` deniega las cinco ANTES del broker). La identidad y el canal los fija el broker, no el asiento.
MCP_GRANTS_V2 = {
    "mcp__soul-v2-gateway__soul_memory_store_v2",
    "mcp__soul-v2-gateway__working_state_update_v2",
    "mcp__soul-v2-gateway__emotional_diary_v2",
    "mcp__soul-v2-gateway__agent_task_v2",
    "mcp__soul-v2-gateway__shadow_chat_send_v2",
}


def test_allow_es_lista_cerrada(settings):
    extra = set(_allow(settings)) - ALLOW_CERRADO - MCP_GRANTS_V2
    assert not extra, f"allow fuera de la lista cerrada (cada entrada nueva se revisa a mano): {sorted(extra)}"


def test_allow_conserva_las_trece_tools_readonly_exactas(settings):
    actual = {item for item in _allow(settings) if item.startswith("mcp__")}
    assert actual == MCP_READONLY_CERRADO | MCP_GRANTS_V2


def test_grants_v2_son_exactamente_los_cinco_del_contrato_3b(settings):
    grants = {item for item in _allow(settings) if item.startswith("mcp__") and item.endswith("_v2")}
    assert grants == MCP_GRANTS_V2, sorted(grants ^ MCP_GRANTS_V2)
    # las tools canónicas de escritura siguen fuera del allow (van por el broker, no directas)
    for canon in ("memory_store", "working_state_update", "agent_task", "emotional_diary", "consent_grant", "system_gateway"):
        assert f"mcp__soul-v2-gateway__{canon}" not in _allow(settings), canon


def test_allow_minimo_para_trabajar(settings):
    allow = _allow(settings)
    for necesario in ("Read", "Grep", "Glob", "Monitor", "mcp__soul-v2-gateway__boot_context", "mcp__soul-v2-gateway__active_recall"):
        assert necesario in allow, f"falta {necesario}: el asiento no podría ni cargar su alma"
    assert any(a.startswith("Bash(/home/dadito/IA/proyecto-seal/scripts/seal_send.py ALICE-V2") for a in allow), \
        "sin seal_send.py ALICE-V2 el asiento no tiene voz en la sombra"
    assert not any("seal_send.py ALICE " in a or "seal_send.py ALICE:" in a for a in allow), "publicaría como ALICE v1"


def test_deny_gana_sobre_allow_en_credenciales(settings):
    # Ningún allow de Read/Bash puede cubrir credenciales aunque un deny las tape: la política no debe depender de la precedencia.
    for a in _allow(settings):
        assert "credentials" not in a and ".agent_session_token" not in a and ".config/seal" not in a


def test_directorios_extra_solo_senuelos(settings):
    extra = settings["permissions"].get("additionalDirectories", [])
    assert extra and all(fnmatch.fnmatch(d, "/tmp/seal-examen-r*") for d in extra), extra


# ── MCP: sólo gateway SOUL v2 como UID aislado, sin bearer en el asiento ─────────────────

def test_mcp_solo_gateway_v2(mcp):
    servers = mcp["mcpServers"]
    assert set(servers) == {"soul-v2-gateway"}, f"servidores inesperados en el asiento: {sorted(servers)}"


def test_mcp_gateway_corre_como_uid_aislado_sin_bearer(mcp):
    s = mcp["mcpServers"]["soul-v2-gateway"]
    assert s["type"] == "stdio"
    assert s["command"] == "/usr/bin/sudo"
    assert s["args"] == [
        "-n", "-u", "alice-v2-lab", "/usr/bin/python3",
        "/opt/alice-v2-owner-runtime/scripts/mcp_gateway_client.py",
        "--socket", "/run/alice-v2-lab/mcp-broker.sock",
    ]
    assert "Authorization" not in json.dumps(s)
    assert "ALICE.token" not in json.dumps(s)


def test_mcp_sin_token_literal(mcp):
    raw = json.dumps(mcp)
    assert "Bearer" not in raw
    assert not re.search(r"Bearer\s+(?!\$\{)[A-Za-z0-9_\-\.]{20,}", raw), "token literal en el MCP del asiento"
    assert "sk-ant-" not in raw


def test_git_lectores_transitivos_denegados(settings):
    """3-sep 16:48 (ADA): Read(ruta sensible) DENEGADO pero `git diff --no-index <ruta>` PERMITIDO,
    y `git show` recupera un token vivo. Los lectores de git son una puerta lateral al Read: fuera del allow, en deny."""
    allow=set(settings["permissions"]["allow"]); deny=set(settings["permissions"]["deny"])
    for tool in ("Bash(git diff:*)","Bash(git show:*)","Bash(git log:*)","Bash(git cat-file:*)","Bash(git grep:*)"):
        assert tool not in allow, tool
        assert tool in deny, tool
    assert "Bash(git status:*)" in allow
