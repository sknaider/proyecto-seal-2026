"""Escritor C10 del cuerpo Codex de ADA: mide por día Lima, escribe bajo RLS, falla ruidoso.

Sin red, sin DB real: la conexión se falsifica. Carril `ada-codex-c10-token-writer-20260908`.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

MESSAGES = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = MESSAGES / "ada_codex_c10_token_writer.py"


@pytest.fixture
def w():
    spec = importlib.util.spec_from_file_location("c10_codex_bajo_prueba", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _evento(ts: str, inp: int, out: int, tipo="token_count") -> str:
    return json.dumps({"timestamp": ts, "type": "event_msg",
                       "payload": {"type": tipo, "info": {"total_token_usage": {"input_tokens": 999999, "output_tokens": 999999},
                                                           "last_token_usage": {"input_tokens": inp, "output_tokens": out}}}})


def _sesiones(tmp_path, carpeta: dt.date, nombre: str, lineas: list[str]) -> pathlib.Path:
    d = tmp_path / "sessions" / f"{carpeta.year:04d}" / f"{carpeta.month:02d}" / f"{carpeta.day:02d}"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rollout-{nombre}.jsonl"
    p.write_text("\n".join(lineas) + "\n")
    return p


DIA = dt.date(2026, 9, 8)


def test_unit_mide_sin_escribir_y_sale_0(tmp_path):
    _sesiones(tmp_path, DIA, "a", [_evento("2026-09-08T15:00:00.000Z", 100, 7)])
    r = subprocess.run([sys.executable, str(SCRIPT), "--json", "--dia", "2026-09-08", "--codex-home", str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["agent"] == "ADA" and d["mode"] == "measure" and d["written"] is False
    assert d["input_tokens"] == 100 and d["output_tokens"] == 7 and d["event_count"] == 1


def test_positivo_suma_last_token_usage_del_dia_en_varios_archivos(w, tmp_path):
    _sesiones(tmp_path, DIA, "a", [_evento("2026-09-08T15:00:00Z", 100, 10), _evento("2026-09-08T16:00:00Z", 50, 5)])
    # carpeta del día UTC siguiente, pero el evento cae el 8 en Lima (UTC-5): 2026-09-09T03:00Z = 22:00 del 8
    _sesiones(tmp_path, DIA + dt.timedelta(days=1), "b", [_evento("2026-09-09T03:00:00Z", 1, 1)])
    res = w.sumar(w.archivos_candidatos(tmp_path / "sessions", DIA), DIA)
    assert res["input_tokens"] == 151 and res["output_tokens"] == 16
    assert res["event_count"] == 3 and res["file_count"] == 2
    assert res["input_tokens"] != 999999, "usa last_token_usage, no el total acumulado"


def test_negativo_eventos_de_otro_dia_lineas_corruptas_y_otros_tipos_no_cuentan(w, tmp_path):
    _sesiones(tmp_path, DIA, "a", [
        _evento("2026-09-09T06:00:00Z", 1000, 1000),          # 01:00 del 9 en Lima: otro día
        _evento("2026-09-08T15:00:00Z", 1000, 1000, tipo="agent_message"),  # otro tipo de evento
        '{"timestamp":"2026-09-08T15:00:00Z","type":"event_msg","payload":{"type":"token_count"',  # corrupta
        _evento("2026-09-08T15:00:00Z", 3, 2),
    ])
    res = w.sumar(w.archivos_candidatos(tmp_path / "sessions", DIA), DIA)
    assert (res["input_tokens"], res["output_tokens"], res["event_count"]) == (3, 2, 1)


def test_negativo_sin_sesiones_mide_cero_y_no_escribe(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--json", "--dia", "2026-09-08", "--codex-home", str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout)
    assert r.returncode == 0 and d["event_count"] == 0 and d["input_tokens"] == 0 and d["written"] is False


class _Conn:
    def __init__(self, registro):
        self.registro = registro

    async def execute(self, sql, *args):
        self.registro.append((" ".join(sql.split()), args))

    async def close(self):
        self.registro.append(("close", ()))


def test_control_write_fija_app_agent_ADA_y_hace_el_upsert_con_los_totales(w):
    registro: list = []

    async def conectar(dsn):
        registro.append(("connect", (dsn,)))
        return _Conn(registro)

    w.escribir("postgresql://centinela@localhost/x", 151, 16, conectar=conectar)
    assert registro[0] == ("connect", ("postgresql://centinela@localhost/x",))
    assert "set_config('app.agent', $1, false)" in registro[1][0] and registro[1][1] == ("ADA",)
    assert "INSERT INTO soul_v3.agent_token_budget" in registro[2][0] and registro[2][1] == ("ADA", 151, 16)
    sql = registro[2][0]
    # Las DOS columnas conservan el mayor valor del día (dos cuerpos escriben la misma fila):
    # un UPSERT que pise una sola de ellas borra lo que midió el otro cuerpo.
    assert sql.count("GREATEST(soul_v3.agent_token_budget.consumed_today_input, $2)") == 1
    assert sql.count("GREATEST(soul_v3.agent_token_budget.consumed_today_output, $3)") == 1
    assert registro[-1][0] == "close"


def test_control_write_que_falla_sale_con_2_y_written_false(w, monkeypatch, capsys):
    monkeypatch.setattr(w, "resolver_dsn", lambda: "postgresql://centinela@localhost/x")

    def explota(dsn, ti, to, conectar=None):
        raise ConnectionRefusedError("no hay base")

    monkeypatch.setattr(w, "escribir", explota)
    rc = w.main(["--write", "--json", "--dia", "2026-09-08", "--codex-home", "/nonexistent-senuelo"])
    d = json.loads(capsys.readouterr().out)
    assert rc == 2 and d["written"] is False and d["error"] == "ConnectionRefusedError" and d["mode"] == "write"


def test_control_la_credencial_no_esta_en_el_codigo():
    import re
    codigo = "\n".join(l.split("#", 1)[0] for l in SCRIPT.read_text().splitlines())
    assert not re.search(r"postgres(ql)?://[^\s:/'\"]+:[^\s@'\"]+@", codigo)
