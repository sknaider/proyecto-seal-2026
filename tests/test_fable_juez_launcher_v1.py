"""Contrato de FABLE JUEZ a demanda (spec docs/specs/SPEC_FABLE_JUEZ_A_DEMANDA_v1.md).

Pruebas estáticas sobre los bytes del lanzador, el vigilante, el extractor y el
frontend: nada se lanza, nada toca DB ni chat. Owner ADA (Claude), 3-sep-2026.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "fable_juez.sh"
WATCH = ROOT / "fable" / "fable_juez_watch.py"
EXPED = ROOT / "fable" / "fable_juez_expediente.py"
PAGE = ROOT / "seal-studio" / "frontend" / "src" / "app" / "v2" / "page.tsx"
FEED = ROOT / "seal-studio" / "frontend" / "src" / "app" / "v2" / "LiveFeed.tsx"
PRESENCE = ROOT / "seal-studio" / "frontend" / "src" / "app" / "api" / "fable-juez" / "presence" / "route.ts"
SPEC = ROOT / "docs" / "specs" / "SPEC_FABLE_JUEZ_A_DEMANDA_v1.md"


@pytest.fixture(scope="module")
def launcher() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


# ---------- lanzador: qué exporta y qué NO hace ----------
def test_launcher_exports_judge_role_and_identity(launcher):
    """qa_positive: rol juez + identidad Bearer, sin tmux."""
    assert "export SEAL_AGENT=FABLE" in launcher
    assert "export SEAL_FABLE_ROLE=juez" in launcher
    assert 'source "$ROOT/seal_identity_env.sh"' in launcher
    assert re.search(r"^\s*(exec\s+)?tmux\b", launcher, re.M) is None  # ningún comando tmux (el comentario puede nombrarlo)
    assert '/memory/end_session.sh FABLE' in launcher


def test_launcher_never_listens_to_general(launcher):
    """qa_negative: el juez no arma monitor del general ni usa el log del monitor de canal."""
    assert "seal_events_FABLE.log" not in launcher
    assert "seal-channel-monitor@FABLE" not in launcher
    assert "aporte valor claro de profesor" not in launcher
    assert "fable_juez_watch.py" in launcher


def test_launcher_prompt_contract(launcher):
    """unit: el prompt dice dictaminar, no reparar; canal propio; veredicto UNA vez en web_chat como review."""
    assert "NO opinás en el canal general" in launcher
    assert "--channel fable-juez" in launcher
    assert "--type review" in launcher
    assert "UNA sola vez al canal general" in launcher
    assert "Dictaminás; no reparás" in launcher or "dictaminás; el dueño ejecuta" in launcher


def test_launcher_default_brain_is_fable_5_1(launcher):
    """qa_positive: William 3-sep 17:47: 'que corra por defecto Fable 1 el modelo que le corresponde'."""
    assert '--model "${FABLE_JUEZ_MODEL:-claude-fable-5-1}"' in launcher
    assert "--model claude-opus-5" not in launcher


def test_launcher_learns_from_ledger(launcher):
    """qa_positive: lee su calibración al arrancar y registra criterio al cerrar cada caso."""
    assert "fable_ledger.py brief" in launcher
    assert "fable_ledger.py add --message-id" in launcher and "--criterio" in launcher


def test_launcher_has_no_family_mcp(launcher):
    """qa_negative: sin herramientas SOUL de la familia (paridad con fable.sh): MCP vacío y estricto."""
    assert '--strict-mcp-config --mcp-config "$ROOT/fable_mcp_empty.json"' in launcher


def test_launcher_single_judge_guard_filters_by_role(launcher):
    """qa_control: el guard de 'un solo juez' busca el rol, no el nombre (no mata al FABLE 24/7 si existiera)."""
    assert 'grep -qx "SEAL_FABLE_ROLE=juez"' in launcher
    assert "--name FABLE" not in launcher.split("Un solo juez")[1].split("Expediente")[0]


# ---------- vigilante ----------
def test_watch_channels_are_exact_and_exclude_fable():
    src = WATCH.read_text(encoding="utf-8")
    assert 'CHANNELS = {"fable-juez", "dm:fable:william"}' in src
    assert 'if str(d.get("from", "")).upper() == "FABLE":' in src
    assert "william_channel.jsonl" in src and "seal_events_FABLE.log" not in src
    assert "flush=True" in src


# ---------- extractor: solo lectura, nunca superusuario ----------
def test_expediente_is_read_only_and_never_superuser():
    src = EXPED.read_text(encoding="utf-8")
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE)\b", src)
    assert "channel='web_chat'" in src
    assert "seal_secrets" not in src  # no cae al rol seal (superusuario)
    assert "seal_studio_db.env" in src


# ---------- frontend ----------
def test_frontend_tab_and_channel():
    page = PAGE.read_text(encoding="utf-8")
    feed = FEED.read_text(encoding="utf-8")
    assert '"fable"' in page and "FABLE JUEZ" in page
    assert '{tab === "fable" && <LiveFeed mode="fable" />}' in page
    assert 'isFable ? "fable-juez"' in feed
    assert 'isFable ? "FABLE"' in feed


def test_presence_counts_only_judge_body():
    src = PRESENCE.read_text(encoding="utf-8")
    assert 'vars.includes("SEAL_AGENT=FABLE") && vars.includes("SEAL_FABLE_ROLE=juez")' in src


def test_pending_fable_review_is_valid_on_demand_trigger():
    """Un manifiesto pendiente designado a FABLE convoca; no firma ni reasigna solo."""
    spec = SPEC.read_text(encoding="utf-8")
    assert 'independent_reviewer: "FABLE"' in spec
    assert 'review.status: "pending"' in spec
    assert "motivo válido de convocatoria" in spec
    assert "no firma ni cambia el estado" in spec
    assert "No se reasigna automáticamente" in spec
    assert "quality/manifests/nexus-credential-paths.json" in spec
    assert "quality/manifests/channel-acl.json" in spec
