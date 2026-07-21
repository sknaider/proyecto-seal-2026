from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "seal_autonomy_guard.py"
)
SPEC = importlib.util.spec_from_file_location("seal_autonomy_guard", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
APPROVAL_GATES = MODULE.APPROVAL_GATES
autonomy_warning = MODULE.autonomy_warning


def test_warns_on_permission_reflex_to_william() -> None:
    examples = (
        "SUIE queda esperando tu OK.",
        "Si me das luz verde, avanzo.",
        "William, ¿me autorizas?",
        "Necesito tu aprobación para continuar.",
        "¿Podemos proceder?",
    )
    assert all(autonomy_warning("William", message) for message in examples)


def test_does_not_warn_on_reporting_or_other_recipients() -> None:
    assert autonomy_warning("William", "SUIE está GREEN y desplegado.") is None
    assert autonomy_warning("William", "No esperaré otra luz verde; ya ejecuté.") is None
    assert autonomy_warning("NEXUS", "¿Podemos proceder con la revisión?") is None


def test_explicit_real_gate_suppresses_warning() -> None:
    assert set(APPROVAL_GATES) == {
        "destructive",
        "external_commitment",
        "scope_change",
        "human_only",
    }
    assert (
        autonomy_warning(
            "William",
            "Necesito tu autorización antes del DELETE masivo.",
            approval_gate="destructive",
        )
        is None
    )
