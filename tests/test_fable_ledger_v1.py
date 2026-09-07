"""Ledger de veredictos de FABLE: parser, clasificación de casos y resultados por manifiesto.
Sin DB: se prueban las funciones puras y manifest_state sobre fixtures. Owner ADA, 3-sep-2026."""
from __future__ import annotations

import json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fable"))
import fable_ledger as L  # noqa: E402


def _row(text, mid=1):
    return {"id": mid, "channel": "web_chat", "content": text, "created_at": datetime.now(timezone.utc)}


def test_positive_parses_conditional_approve_with_manifest():
    """qa_positive: veredicto condicionado + manifiesto + condición."""
    p = L.parse_review(_row("## 1 · `x-20260902` → **APPROVE CONDICIONADO**\nver quality/manifests/x-20260902.json\n**Condición:** un test que vea el call."))
    assert p and p["veredicto"] == "APPROVE_CONDICIONADO"
    assert p["caso_ref"] == "quality/manifests/x-20260902.json" and p["caso_tipo"] == "manifest"
    assert p["condiciones"] and "test" in p["condiciones"]


def test_negative_review_without_verdict_is_skipped():
    """qa_negative: una medición sin fallo formal no entra al ledger."""
    assert L.parse_review(_row("Henry: los PDF están BIEN. Medido uno por uno.")) is None


def test_control_reject_and_change_id():
    """qa_control: REJECT con change_id (sin ruta) se clasifica como change_id."""
    p = L.parse_review(_row("## 2 · `nexus-credential-paths-20260903` → **REJECT** (como fue entregado)"))
    assert p["veredicto"] == "REJECT" and p["caso_tipo"] == "change_id" and p["caso_ref"] == "nexus-credential-paths-20260903"


def test_unit_manifest_state_reads_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "ROOT", tmp_path)
    d = tmp_path / "quality" / "manifests"; d.mkdir(parents=True)
    (d / "m.json").write_text(json.dumps({"review": {"status": "approved", "receipt": {"reviewer": "fable"}}}))
    st = L.manifest_state("quality/manifests/m.json")
    assert st["status"] == "approved" and st["reviewer"] == "FABLE" and len(st["sha"]) == 64
    assert L.manifest_state("quality/manifests/no.json") is None


def test_unit_report_math():
    cal = {"tabla": {"REJECT": {"confirmado": 3, "refutado": 1, "pendiente": 2}}, "criterios": []}
    out = L.fmt_report(cal)
    assert "REJECT" in out and "acierto sobre decididos: 75 %" in out


def test_script_is_read_only_outside_its_table():
    import re
    src = (ROOT / "fable" / "fable_ledger.py").read_text(encoding="utf-8")
    targets = re.findall(r"(?:INSERT INTO|UPDATE)\s+([a-z_]+\.[a-z_]+)", src)
    assert targets and set(targets) == {"fable.veredictos"}, targets
    assert "DELETE FROM" not in src.upper()
    assert "seal_secrets" not in src


def test_positive_two_verdicts_in_one_message_give_two_rows():
    """qa_positive (hallazgo FABLE #147183): un mensaje con dos casos = dos filas, cada una con su caso."""
    txt = ("# VEREDICTO — dos manifiestos\n\n## 1 · `channel-acl-20260902` → **APPROVE CONDICIONADO**\ncuerpo\n"
           "**Condición:** un test que vea el call.\n\n## 2 · `nexus-credential-paths-20260903` → **REJECT** (como fue entregado)\ncuerpo 2\n")
    rows = L.parse_reviews(_row(txt, mid=147118))
    assert [(r["caso_ref"], r["veredicto"]) for r in rows] == [
        ("channel-acl-20260902", "APPROVE_CONDICIONADO"), ("nexus-credential-paths-20260903", "REJECT")]


def test_control_single_verdict_message_still_one_row():
    rows = L.parse_reviews(_row("**APPROVE CONDICIONADO**\nver quality/manifests/x.json", mid=5))
    assert len(rows) == 1 and rows[0]["caso_tipo"] == "manifest"


def test_unit_curve_counts_cases_not_rows():
    src = (ROOT / "fable" / "fable_ledger.py").read_text(encoding="utf-8")
    assert "DISTINCT ON (coalesce(regexp_replace(caso_ref" in src   # pedido FABLE #147192: no contar dos veces
    assert "una fila con caso (la hermana ya lo resolvió)" in src


def test_unit_add_lets_fable_fix_case_ref():
    src = (ROOT / "fable" / "fable_ledger.py").read_text(encoding="utf-8")
    assert "UPDATE fable.veredictos SET caso_ref=$2, caso_tipo=$3, veredicto=$4" in src


def test_negative_conversation_mentioning_verdict_words_is_not_a_verdict():
    """qa_negative: una respuesta con 'APPROVE_CONDICIONADO' en un bloque de código no es un fallo."""
    txt = "ADA — ledger recibido.\n```text\n147049 lifecycle APPROVE_CONDICIONADO fila 39\n147113 credential REJECT fila 42\n```\nacepto el dato."
    assert L.parse_reviews({"id": 9, "channel": "fable-juez", "content": txt, "created_at": datetime.now(timezone.utc), "message_type": "conversation"}) == []


def test_positive_review_type_or_bold_verdict_is_formal():
    assert L.is_formal({"content": "algo", "message_type": "review"})
    assert L.is_formal({"content": "## 1 · `x` → **APPROVE CONDICIONADO**", "message_type": "conversation"})
    assert not L.is_formal({"content": "yo diría approve, pero", "message_type": "conversation"})
