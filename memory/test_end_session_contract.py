from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).with_name("end_session.sh")


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_end_session_parses_body_before_running() -> None:
    source = _source()
    assert "main() {" in source
    assert 'main "$@"' in source


def test_end_session_contains_only_session_bound_capture_step() -> None:
    source = _source()
    assert 'step "session_capture"' in source
    assert 'step "soul_reflect"' not in source
    assert 'step "soul_backup"' not in source
    assert 'step "kairos' not in source


def test_end_session_fails_closed_without_agent_credential() -> None:
    source = _source()
    assert '[ ! -r "$DSN_FILE" ]' in source
    assert "NO se capturo nada" in source
    assert "exit 1" in source


def test_end_session_uses_the_agent_runtime_role_not_nerves() -> None:
    """El hook debe correr con mcp_runtime_<ag>, el unico rol con grants de alma.

    `svc_soul_nerves_<ag>` no tiene NINGUN grant sobre las 6 tablas del alma: el
    hook fallaba con `permission denied for table event_log`. Fijar la fuente de
    la credencial evita que una edicion futura vuelva al rol sin permisos --
    fallaria en el CIERRE, sin nadie mirando. (NEXUS 29-ago-2026.)
    """
    source = _source()
    assert "/home/dadito/.config/seal/mcp_agents/${AGENT_LC}.dsn" in source
    # OJO: no basta con `"soul_nerves_" not in source` -- el archivo MENCIONA ese rol
    # en el comentario que explica por que NO se usa. El test miraria la mencion y no
    # el uso, que es el error que venimos cazando todo el dia. Se ancla al PATH.
    assert "soul_nerves_${AGENT_LC}_db.env" not in source


def test_complete_claim_counts_the_gated_steps_at_runtime() -> None:
    source = _source()
    assert "TOTAL_STEPS=0" in source
    assert "TOTAL_STEPS=$((TOTAL_STEPS + 1))" in source
    assert 'if [ "$TOTAL_STEPS" -eq 1 ]; then STEP_WORD="paso"; else STEP_WORD="pasos"; fi' in source
    assert "${#FAILED[@]} fallos / $TOTAL_STEPS $STEP_WORD" in source
    assert "fallos / 1 paso" not in source
    assert "COMPLETE" in source


def test_end_session_declares_its_step_count_instead_of_counting_the_run():
    """El denominador lo declara el contrato, no la corrida.

    Sin ``EXPECTED_STEPS`` borrar una linea ``step`` bajaba ``TOTAL_STEPS`` y el
    cierre seguia diciendo COMPLETE: el mensaje medía lo que corrió, nunca lo que
    debía correr.  El mutante que revierte este gate muere acá.
    """
    source = _source()
    assert "EXPECTED_STEPS=" in source, "el contrato no declara cuantos pasos espera"
    assert '"$TOTAL_STEPS" -ne "$EXPECTED_STEPS"' in source, (
        "no se compara lo corrido contra lo declarado antes de aceptar COMPLETE"
    )
    declared = source.index("EXPECTED_STEPS=")
    gate = source.index('"$TOTAL_STEPS" -ne "$EXPECTED_STEPS"')
    complete = source.index("Session capture COMPLETE")
    assert declared < gate < complete, (
        "el gate debe evaluarse ANTES de poder declarar COMPLETE"
    )
