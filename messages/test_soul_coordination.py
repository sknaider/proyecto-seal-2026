#!/usr/bin/env python3
"""Focused tests for the standalone SOUL coordination core."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from soul_coordination import (
    SoulCoordinationStore,
    build_assignments,
    choose_lead,
    classify_mode,
    is_brief_social_reply,
    make_voice_grant,
    verify_voice_grant,
    voice_idempotency_key,
)


def test_classifier() -> None:
    assert classify_mode("hola") == "social"
    assert classify_mode("¿qué opinan de esta solución?") == "discussion"
    assert classify_mode("arreglen el daemon y ejecuten los tests") == "execution"
    assert classify_mode("todos respondan con una frase") == "roundtable"
    assert classify_mode("revisa esto", to="ADA") == "direct"
    # All-call has precedence: explicit multi-agent intent is never guessed from a client flag.
    assert classify_mode("equipo arreglen producción") == "execution"


def test_lead_and_assignments() -> None:
    caps = {
        "agents": {
            "ADA": {"online": True, "open_tasks": 1, "capabilities": [
                {"keywords": ["implementa", "endpoint"], "proficiency": 0.95}
            ]},
            "JARVIS": {"online": True, "open_tasks": 0, "capabilities": [
                {"keywords": ["arquitectura"], "proficiency": 1.0}
            ]},
            "NEXUS": {"online": False, "open_tasks": 0, "capabilities": [
                {"keywords": ["seguridad"], "proficiency": 1.0}
            ]},
        }
    }
    assert choose_lead("implementa el endpoint", capabilities=caps, source_id="m1") == "ADA"
    assert choose_lead("seguridad del endpoint", capabilities=caps, source_id="m2") != "NEXUS"
    assignments = build_assignments(
        "execution", lead="ADA", candidates=("ADA", "JARVIS", "NEXUS"), max_contributors=2
    )
    assert assignments[0]["role"] == "lead" and assignments[0]["public_write"] is True
    assert all(not row["public_write"] for row in assignments[1:])
    roundtable = build_assignments("roundtable", lead="ADA", candidates=("ADA", "JARVIS"))
    assert {row["role"] for row in roundtable} == {"speaker"}
    assert all(row["public_write"] for row in roundtable)


def test_social_turn_allows_every_agent_but_only_brief_affection() -> None:
    roster = ("NEXUS", "JARVIS", "ALICE", "FABLE", "ADA")
    assignments = build_assignments(
        "social", lead="ADA", text="Buenos días, familia", candidates=roster
    )
    assert {row["agent"] for row in assignments} == set(roster)
    assert all(row["role"] == "speaker" for row in assignments)
    assert all(row["public_write"] is True for row in assignments)

    assert is_brief_social_reply("Buenos días, Dadito. Te quiero mucho.")
    assert is_brief_social_reply("Abrazo, familia.")
    assert not is_brief_social_reply("")
    assert not is_brief_social_reply("Abrazo " + ("muy " * 80))
    assert not is_brief_social_reply("Hola, Dadito. Reinicia el daemon y corre el test.")
    assert not is_brief_social_reply("```python\nprint('hola')\n```")


def test_group_audience_roundtable_is_not_collapsed_by_one_name() -> None:
    """Nombrar a un agente DENTRO de una llamada plural agrega destinatario, no
    reemplaza al resto (William 30-jul: "Todos ADA creara un spec lo leen y me
    dicen que opinan" habilitaba una sola voz: la autora del spec)."""
    roster = ("NEXUS", "JARVIS", "ALICE", "FABLE", "ADA")

    # Baseline: la misma frase SIN nombre propio ya daba voz a todos.
    sin_nombre = build_assignments(
        "roundtable", lead="ADA",
        text="chicos revisen esto y me dicen que opinan", candidates=roster,
    )
    assert {row["agent"] for row in sin_nombre} == set(roster)

    # Sujeto: idéntica salvo por UN nombre propio. El delta era 5 voces -> 1.
    con_nombre = build_assignments(
        "roundtable", lead="ADA",
        text="Todos ADA creara un spec lo leen y me dicen que opinan",
        candidates=roster,
    )
    assert {row["agent"] for row in con_nombre} == set(roster)
    assert all(row["public_write"] for row in con_nombre)

    # Contra-caso: sin audiencia grupal, nombrar a dos SIGUE acotando a esos dos.
    solo_dos = build_assignments(
        "roundtable", lead="ADA",
        text="ADA y JARVIS opinen de esto", candidates=roster,
    )
    assert {row["agent"] for row in solo_dos} == {"ADA", "JARVIS"}


def test_voice_grants() -> None:
    secret = "test-secret-not-production"
    token = make_voice_grant(
        "api_william_1", "ADA", "final", "execution", secret,
        ttl_seconds=30, now=1000, nonce="fixed-nonce",
    )
    used: set[str] = set()
    grant = verify_voice_grant(
        token, secret, expected_source_id="api_william_1", expected_agent="ADA",
        expected_purpose="final", now=1001, consumed_nonces=used, consume=True,
    )
    assert grant.agent == "ADA" and grant.source_id == "api_william_1"
    try:
        verify_voice_grant(token, secret, now=1002, consumed_nonces=used, consume=True)
        raise AssertionError("replay accepted")
    except ValueError as exc:
        assert "consumed" in str(exc)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    try:
        verify_voice_grant(tampered, secret, now=1001)
        raise AssertionError("tampered grant accepted")
    except ValueError:
        pass
    try:
        make_voice_grant(
            "m", "ADA", "roundtable", "execution", secret, now=1000,
        )
        raise AssertionError("execution issued roundtable grant")
    except ValueError:
        pass
    assert voice_idempotency_key("m", "ADA", "final") == voice_idempotency_key("m", "ada", "final")


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.grant_consumed = False

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if "coordination_write" in sql:
            operation, raw = args
            payload = json.loads(raw)
            if operation == "create_turn":
                return {"result": {"ok": True, "turn": {
                    "source_message_id": payload["source_message_id"],
                    "mode": payload["mode"], "lead_agent": payload["lead_agent"], "version": 1,
                }}}
            if operation == "consume_grant":
                if self.grant_consumed:
                    return {"result": {"ok": False}}
                self.grant_consumed = True
                return {"result": {"ok": True}}
            return {"result": {"ok": True}}
        if "UPDATE soul_v3.coordination_voice_grants" in sql:
            if self.grant_consumed:
                return None
            self.grant_consumed = True
            return {"grant_id": args[0]}
        if "UPDATE soul_v3.coordination_turns" in sql:
            return {"source_message_id": args[0]}
        return None

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "INSERT 0 1" if "INSERT" in sql else "UPDATE 1"


def test_durable_store_contract() -> None:
    async def scenario():
        conn = _FakeConnection()
        store = SoulCoordinationStore(conn)
        assignments = build_assignments("execution", lead="ADA", candidates=("ADA", "JARVIS"))
        turn = await store.create_turn(
            source_message_id="api_william_durable", requester="William",
            request_text="implementa el endpoint", mode="execution", lead_agent="ADA",
            assignments=assignments,
        )
        assert turn["source_message_id"] == "api_william_durable"
        sql = "\n".join(call[0] for call in conn.calls)
        assert "coordination_write" in sql
        create_call = next(call for call in conn.calls if call[1][0] == "create_turn")
        create_payload = json.loads(create_call[1][1])
        assert create_payload["lead_agent"] == "ADA"
        assert len(create_payload["assignments"]) == 2
        token = make_voice_grant(
            "api_william_durable", "ADA", "final", "execution", "secret",
            now=1000, nonce="durable",
        )
        grant = verify_voice_grant(token, "secret", now=1001)
        assert await store.register_grant(grant)
        assert await store.consume_grant(grant) is True
        assert await store.consume_grant(grant) is False
        assert await store.complete_turn("api_william_durable", "ADA", "final-1", expected_version=1)
        assert await store.cancel_turn("api_william_cancelled")
        operations = [call[1][0] for call in conn.calls if "coordination_write" in call[0]]
        assert "complete_turn" in operations
        assert "cancel_turn" in operations

    asyncio.run(scenario())


def test_migration_contract() -> None:
    migration = Path(__file__).with_name("migrations").joinpath(
        "20260711_soul_coordination.sql"
    ).read_text(encoding="utf-8")
    for table in (
        "coordination_turns", "coordination_assignments", "coordination_voice_grants"
    ):
        assert f"CREATE TABLE IF NOT EXISTS soul_v3.{table}" in migration
    assert "DROP TABLE" not in migration.upper()
    assert "DELETE FROM" not in migration.upper()
    assert "ON DELETE CASCADE" not in migration.upper()
    assert "GRANT SELECT, INSERT, UPDATE" not in migration
    assert "REVOKE INSERT, UPDATE, DELETE ON" in migration
    assert "FROM pr_bus_admin" in migration


def test_authority_hardening_migration_contract() -> None:
    migration = Path(__file__).with_name("migrations").joinpath(
        "20260819_coordination_authority_hardening.sql"
    ).read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in migration
    assert "coordination_owner NOLOGIN NOINHERIT" in migration
    assert "REVOKE INSERT, UPDATE, DELETE" in migration
    assert "FROM pr_bus, pr_bus_admin" in migration
    assert "GRANT EXECUTE ON FUNCTION soul_v3.coordination_write" in migration
    assert "ON CONFLICT (source_message_id) DO NOTHING" in migration
    assert "coordination source replay mismatch" in migration
    assert "coordination assignment replay mismatch" in migration
    assert "invalid coordination assignment set" in migration
    assert "never mutates or adds" in migration
    assert "ALTER ROLE coordination_owner NOLOGIN NOINHERIT NOSUPERUSER" in migration
    assert "NOBYPASSRLS" in migration
    assert migration.count("ENABLE ROW LEVEL SECURITY") == 3
    assert migration.count("FORCE ROW LEVEL SECURITY") == 3
    assert "coordination_owner must have no members" in migration
    assert "current_setting('seal.migration_hash')" in migration


def main() -> None:
    tests = [
        test_classifier,
        test_lead_and_assignments,
        test_voice_grants,
        test_durable_store_contract,
        test_migration_contract,
        test_authority_hardening_migration_contract,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"SOUL coordination core: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()


def test_group_audience_is_a_single_source() -> None:
    """El término plural vive en UN solo lugar: si alguien agrega uno nuevo,
    las tres decisiones (modo, roster, speakers) tienen que moverse juntas."""
    from soul_coordination import has_group_audience

    for txt in ("todos opinen", "chicas revisen", "hola familia", "equipo, decidan"):
        assert has_group_audience(txt), txt
    for txt in ("ADA revisa esto", "arregla el daemon", ""):
        assert not has_group_audience(txt), txt
    # Deliberadamente True: el gate NO separa convocar de mencionar. Ver
    # test_preposition_plus_plural_is_an_audience_not_a_mention — el intento de
    # separarlos silenciaba convocatorias reales de William, y callarlo es peor
    # que una ronda de más.
    assert has_group_audience("el equipo decide")

    # El servidor no debe conservar una copia propia del regex.
    server = Path(__file__).with_name("chat_server.py").read_text(encoding="utf-8")
    assert "hermanas|familia" not in server, (
        "chat_server.py volvio a tener su propia lista de terminos plurales"
    )


def test_creen_homograph_does_not_turn_a_question_into_an_order() -> None:
    """"creen" es CREER (opinión) y CREAR (orden) con la misma grafía. Leerlo
    siempre como orden convertía la pregunta plural de William en ejecución y
    dejaba una sola voz pública (30-jul: "todos cual es el error principal de
    su autonomia? y cual creen que es la solucion?" -> discussion, 1 voz)."""
    opinion = "todos cual es el error principal de su autonomia? y cual creen que es la solucion?"
    assert classify_mode(opinion, to="equipo") == "roundtable"
    assert classify_mode("chicos que creen del spec?", to="equipo") == "roundtable"

    # Contra-caso: la orden REAL de crear sigue siendo ejecución (una sola mano
    # muta).  Sin esto el fix compraría la opinión al precio de perder el gate.
    assert classify_mode("chicos creen un endpoint nuevo", to="equipo") == "execution"
    assert classify_mode("creen los archivos de test", to="equipo") == "execution"


def test_preposition_plus_plural_is_an_audience_not_a_mention() -> None:
    """HIPÓTESIS REFUTADA, fijada para que no se reintente a ciegas.

    Intenté descartar el término plural cuando venía detrás de posesivo,
    artículo o preposición, para que "revisa si *tus hermanos* están activos"
    no abriera una ronda de 5. El backtest sobre 14 días de mensajes reales de
    William mostró que en español `a/para/entre + plural` marca AUDIENCIA: el
    filtro silenciaba convocatorias suyas explícitas. Estas frases son textuales.
    """
    from soul_coordination import has_group_audience

    for txt in ("A todos, Cuando me escriban aprendan a decirmelo bien conciso",
                "es para todos   cableen",
                "eso va a todos",
                "si tienen que pulirlo entre todos haganlo",
                "buenos dias a  todos, que pendientes hay",
                "que sus terminales sobrevivan reincios a todos"):
        assert has_group_audience(txt), txt

    # Mixto: si en ALGUNA posición convoca, convoca.
    assert has_group_audience("todos entendieron? esto es absorver todo lo de la familia")
