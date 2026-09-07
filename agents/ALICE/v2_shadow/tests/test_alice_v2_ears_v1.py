"""Oídos propios de ALICE v2 (orden William 4-sep-2026 11:16).

Contrato: (a) el wrapper filtra como ALICE (los eventos van a ALICE) pero escribe el log de ALICE-V2, legible sólo por el
grupo del asiento (0640, chgrp alice-v2-lab), singleton por flock, y no toca al monitor de v1; (b) el launcher del asiento
arranca un Monitor persistente sobre ese log con flock y publica por shadow_chat_send_v2, sin modo consulta.
"""
from __future__ import annotations
import os, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
WRAP = Path(os.environ.get("SEAL_EARS_WRAPPER", ROOT / "messages/seal_alice_v2_ears.sh"))
LAUNCH = Path(os.environ.get("SEAL_EARS_LAUNCHER", ROOT / "agents/ALICE/v2_shadow/alice_v2_isolated.sh"))
UNIT = Path(os.environ.get("SEAL_EARS_UNIT", Path.home() / ".config/systemd/user/seal-alice-v2-ears.service"))


def _w(): return WRAP.read_text(encoding="utf-8")
def _l(): return LAUNCH.read_text(encoding="utf-8")


def test_unit_wrapper_filtra_como_ALICE_y_escribe_el_log_de_V2():
    w = _w()
    assert re.search(r'^AGENT="ALICE"', w, re.M), "el filtro debe correr con --agent ALICE: los eventos van a ALICE"
    assert 'OUT="/tmp/seal_events_ALICE-V2.log"' in w
    assert "--agent '$AGENT'" in w and ">> '$OUT'" in w


def test_positivo_el_log_es_solo_del_grupo_del_asiento():
    w = _w()
    assert 'GROUP="alice-v2-lab"' in w
    assert 'stat -c %G "$OUT")" != "$GROUP"' in w and '!= "640"' in w, "los DM de ALICE van descifrados: el wrapper verifica grupo + 0640 y sale si no cumple"
    assert "chgrp" not in w.split("# dadito NO es miembro")[0].split("umask 027")[1] if "chgrp" in w else True
    assert "umask 027" in w


def test_positivo_singleton_por_flock():
    w = _w()
    assert re.search(r'exec flock -n "\$LOCK"', w), "sin flock un segundo tail duplica cada evento"


def test_negativo_no_toca_al_monitor_de_v1():
    w = _w()
    assert "seal-channel-monitor@ALICE" not in w.replace("# NO toca seal-channel-monitor@ALICE", "")
    assert "seal_events_ALICE.log" not in w, "el log de v1 es de v1"


def test_positivo_launcher_en_modo_monitor_con_flock_y_persistente():
    l = _l()
    assert "MODO MONITOR" in l and "MODO CONSULTA" not in l
    assert "flock -n /tmp/seal_monitor_connect_ALICE-V2.lock tail -n 0 -F /tmp/seal_events_ALICE-V2.log" in l
    assert "persistent: true" in l


def test_positivo_launcher_publica_por_la_tool_no_por_seal_send():
    l = _l()
    assert "shadow_chat_send_v2" in l
    assert "seal_send.py ALICE-V2 <destino>" not in l


def test_positivo_auto_boot_arranca_el_monitor_antes_del_poll():
    l = _l()
    boot = l[l.index("[AUTO-BOOT"):]
    # La frase tiene que ser AFIRMATIVA y estar antes del poll: un "(3) NO arranca el Monitor"
    # conservaba el orden y pasaba el test viejo (mutante sobreviviente, 4-sep).
    assert "(3) arranca el Monitor persistente" in boot, "el paso 3 debe ORDENAR arrancar el Monitor"
    assert boot.index("(3) arranca el Monitor persistente") < boot.index("webchat_poll"), (
        "primero los oidos, despues ponerse al dia"
    )


def test_negativo_el_launcher_nunca_apunta_al_log_de_v1():
    """Toda mencion del firehose en el prompt es el log de v2; el de v1 lo lee la ALICE vieja."""
    l = _l()
    import re
    logs = set(re.findall(r"/tmp/seal_events_[A-Za-z0-9_-]+\.log", l))
    assert logs == {"/tmp/seal_events_ALICE-V2.log"}, f"logs mencionados: {sorted(logs)}"


def test_unit_systemd_es_singleton_y_reinicia():
    if not UNIT.exists():
        import pytest; pytest.skip("unidad no instalada en este host")
    u = UNIT.read_text(encoding="utf-8")
    assert "ExecStart=/home/dadito/IA/proyecto-seal/messages/seal_alice_v2_ears.sh" in u
    assert "Restart=always" in u and "PartOf=seal-chat.service" in u


def test_control_no_vacuo():
    assert "MODO MONITOR" not in "un launcher sin oidos"


# --- Politica del asiento: el permiso del Monitor, medido diferencialmente el 4-sep -----------
# Medido como alice-v2-lab con `claude -p --permission-mode dontAsk`:
#   deny Bash(tail:*) + allow del comando exacto  -> DENEGADO  (el deny gana)
#   sin deny + allow del comando exacto           -> PERMITIDO
#   sin deny + otro comando tail                  -> DENEGADO  (dontAsk niega lo no permitido)
# Por eso quitar el deny de tail NO abre lectura arbitraria: la abriria un allow amplio.

_CMD_MONITOR = (
    "flock -n /tmp/seal_monitor_connect_ALICE-V2.lock "
    "tail -n 0 -F /tmp/seal_events_ALICE-V2.log"
)


def _politica():
    import json
    ruta = os.environ.get(
        "SEAL_EARS_POLICY",
        str(ROOT / "agents/ALICE/v2_shadow/settings_alice_v2_isolated.json"),
    )
    return json.loads(Path(ruta).read_text())["permissions"]


def test_positivo_la_politica_permite_el_comando_exacto_del_monitor():
    perm = _politica()
    assert f"Bash({_CMD_MONITOR})" in perm["allow"], (
        "sin el allow literal exacto el asiento no puede armar sus oidos"
    )
    assert "Monitor" in perm["allow"]


def test_negativo_el_deny_de_tail_no_puede_volver_porque_le_gana_al_allow():
    perm = _politica()
    assert "Bash(tail:*)" not in perm["deny"], (
        "medido: el deny vence al allow exacto y deja sorda a v2"
    )


def test_negativo_no_se_abrio_la_lectura_arbitraria_con_un_allow_amplio():
    perm = _politica()
    amplios = [r for r in perm["allow"] if r in ("Bash(tail:*)", "Bash(flock:*)", "Bash")]
    assert not amplios, f"allow demasiado amplio: {amplios}"
