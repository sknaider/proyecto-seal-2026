from __future__ import annotations

import sys
from pathlib import Path


MESSAGES = Path(__file__).parents[1]
if str(MESSAGES) not in sys.path:
    sys.path.insert(0, str(MESSAGES))

import flood_form_gate


def test_real_receipts_are_exemptible() -> None:
    accepted = [
        "Recibido.",
        "Recibido, Dadito. En ello.",
        "Entendido, William — trabajando.",
        "Te leí; ya lo tomo.",
        "ACK — working on it.",
    ]
    assert all(flood_form_gate.is_receipt_ack(text) for text in accepted)


def test_short_substantive_messages_are_not_receipts() -> None:
    rejected = [
        "Recibido. Implementé el fix, reinicié el daemon y las 54 pruebas pasan.",
        "El servicio está verde: 18/18 dependencias y 9/9 identidades.",
        "Recibido: la causa fue el token del namespace equivocado.",
        "Recibido\n- prueba 1\n- prueba 2",
        "Recibido, revisá https://example.invalid/evidencia",
    ]
    assert not any(flood_form_gate.is_receipt_ack(text) for text in rejected)


def test_length_is_not_the_ack_classifier() -> None:
    assert not flood_form_gate.is_receipt_ack("Recibido. " + "en ello " * 50)
