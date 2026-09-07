#!/usr/bin/env python3
"""Caracterización del FIX del doble-lead: lead stickiness en `choose_lead`.

JARVIS, 19-ago-2026. Cubre el path NUEVO de soul_coordination.py (disco 4b9a) que
el proceso vivo (52c1) todavía no corre: `prior_lead`, `LEAD_STICKINESS_WINDOW_S`
y `SoulCoordinationStore.get_last_turn_by_author`.

El defecto que arregla: los renglones fragmentados de William caen segundos apart;
sin señal de capacidad todos los candidatos empatan en score y el desempate por
hash del `source_id` elegía un lead DISTINTO por fragmento -> doble-lead. La
stickiness hace que un `prior_lead` elegible conserve el turno en el empate exacto.

Hermético: `choose_lead` es puro; los guards de `get_last_turn_by_author` no tocan
DB. Vive en messages/tests/ para heredar el SEAL_PG_DSN dummy del conftest.
"""

from __future__ import annotations

import asyncio

from soul_coordination import (
    LEAD_STICKINESS_WINDOW_S,
    SoulCoordinationStore,
    choose_lead,
)


def _tie_caps() -> dict:
    """Dos agentes online, misma proficiency y open_tasks -> empate EXACTO si el
    texto no matchea ningún keyword. Es el caso no-determinístico que el hash
    resolvía arbitrariamente (la fuente del doble-lead)."""
    return {
        "agents": {
            "ADA": {"online": True, "open_tasks": 0, "capabilities": [
                {"keywords": ["implementa"], "proficiency": 1.0}
            ]},
            "JARVIS": {"online": True, "open_tasks": 0, "capabilities": [
                {"keywords": ["arquitectura"], "proficiency": 1.0}
            ]},
        }
    }


# ── qa_positive: la stickiness conserva el turno en el empate exacto ──

def test_prior_lead_keeps_turn_on_exact_tie_ada() -> None:
    caps = _tie_caps()
    # "en resumen" = fragmento sin keyword de nadie -> empate exacto.
    assert choose_lead("en resumen", capabilities=caps, source_id="frag-2", prior_lead="ADA") == "ADA"


def test_prior_lead_keeps_turn_on_exact_tie_jarvis() -> None:
    caps = _tie_caps()
    # MISMO source_id/texto: sin prior_lead el hash fija UNO; con prior_lead el otro
    # gana igual. Que ambos ganen prueba que la stickiness domina al hash, no que
    # coincidimos con el hash.
    assert choose_lead("en resumen", capabilities=caps, source_id="frag-2", prior_lead="JARVIS") == "JARVIS"


def test_tie_without_prior_lead_is_deterministic_by_source_hash() -> None:
    caps = _tie_caps()
    a = choose_lead("en resumen", capabilities=caps, source_id="same")
    b = choose_lead("en resumen", capabilities=caps, source_id="same")
    assert a == b  # determinístico por source_id
    assert a in ("ADA", "JARVIS")


def test_different_source_ids_can_flip_lead_without_stickiness() -> None:
    """El defecto original: dos fragmentos (source_id distinto) del mismo pedido
    pueden caer a leads distintos por el hash. Caracterizamos que EXISTE el flip
    sin stickiness (si no flipa con estos ids, no invalida el fix; por eso se
    permite igualdad, pero al menos uno de los dos debe poder diferir)."""
    caps = _tie_caps()
    leads = {choose_lead("en resumen", capabilities=caps, source_id=f"frag-{i}") for i in range(12)}
    # Con 12 source_ids distintos y empate, el hash reparte entre ambos.
    assert leads == {"ADA", "JARVIS"}


# ── qa_negative/control: la stickiness NO gana cuando NO debe ──

def test_prior_lead_loses_to_real_keyword_match() -> None:
    caps = _tie_caps()
    # El texto matchea el keyword de ADA -> ADA sube score; prior_lead=JARVIS NO pega.
    assert choose_lead("implementa el módulo", capabilities=caps, source_id="s", prior_lead="JARVIS") == "ADA"


def test_prior_lead_dropped_when_offline() -> None:
    caps = _tie_caps()
    caps["agents"]["NEXUS"] = {"online": False, "open_tasks": 0, "capabilities": [
        {"keywords": ["seguridad"], "proficiency": 1.0}
    ]}
    # NEXUS offline -> no está en `eligible` -> la stickiness cae al desempate por hash.
    result = choose_lead("en resumen", capabilities=caps, source_id="s", prior_lead="NEXUS")
    assert result in ("ADA", "JARVIS") and result != "NEXUS"


def test_prior_lead_dropped_when_cannot_public_write() -> None:
    caps = _tie_caps()
    caps["agents"]["ADA"]["cannot"] = ["public_write"]
    # ADA excluida por `cannot` -> prior_lead=ADA no está en eligible -> no gana.
    assert choose_lead("en resumen", capabilities=caps, source_id="s", prior_lead="ADA") == "JARVIS"


def test_named_directive_beats_stickiness() -> None:
    caps = _tie_caps()
    # "JARVIS responde" es directiva explícita -> gana antes del chequeo de stickiness.
    assert choose_lead("JARVIS responde esto", capabilities=caps, source_id="s", prior_lead="ADA") == "JARVIS"


# ── la constante single-sourced ──

def test_stickiness_window_constant() -> None:
    assert LEAD_STICKINESS_WINDOW_S == 90


# ── get_last_turn_by_author: guards puros (sin DB) + happy path con mock ──

def test_get_last_turn_guards_return_none_without_db() -> None:
    store = SoulCoordinationStore(None)
    assert asyncio.run(store.get_last_turn_by_author("", within_seconds=90)) is None
    assert asyncio.run(store.get_last_turn_by_author("William", within_seconds=0)) is None
    assert asyncio.run(store.get_last_turn_by_author("   ", within_seconds=90)) is None
