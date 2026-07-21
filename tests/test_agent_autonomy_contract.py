from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _normalized(relative: str) -> str:
    return " ".join(_read(relative).replace("`", "").split())


def test_common_boot_contract_requires_execution_without_second_green() -> None:
    contract = _normalized("CLAUDE.md")
    required = (
        "Autonomía operativa y obediencia a William",
        "No volver a pedirla",
        "NO preguntar. Ejecutar y reportar evidencia.",
        "el owner integra y decide el cierre",
        "operación destructiva o difícil de recuperar",
        "cambio material del objetivo pedido por William",
        "RECEIVED -> EXECUTING -> TESTING -> VERIFIED -> COMPLETED",
        "no cerrar en propuesta",
        "BLOCKED solo es válido con un impedimento real",
        "código + restart + healthcheck",
    )
    assert all(clause in contract for clause in required)


def test_common_contract_overrides_legacy_permission_wording() -> None:
    contract = _normalized("CLAUDE.md")
    assert "esta regla común prevalece sobre identidades" in contract
    assert "Esas frases describen la cadena de mando, no una obligación de pedir permiso" in contract


def test_autonomy_contract_preserves_real_escalation_gates() -> None:
    contract = _read("CLAUDE.md")
    for gate in ("DELETE", "DROP", "rm -rf", "compromiso legal/clínico"):
        assert gate in contract
    assert "el silencio de William nunca" in contract
    assert "confirmación explícita del scope exacto" in contract


def test_permission_reflex_is_blocked_by_outbound_guard() -> None:
    contract = _normalized("CLAUDE.md")
    assert "scripts/seal_send.py bloquea" in contract
    assert "--approval-gate destructive|external_commitment|scope_change|human_only" in contract
