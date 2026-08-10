from pathlib import Path

import gate_arranque_clon as gate


def test_projection_path_prefers_instance_scope(tmp_path: Path) -> None:
    default = tmp_path / "technical.sqlite3"
    default.write_bytes(b"default")
    scoped = tmp_path / "technical_JARVIS-u116.sqlite3"
    scoped.write_bytes(b"scoped")

    assert gate.ruta_proyeccion_instancia("JARVIS", "u116", default=default) == scoped
    assert gate.ruta_proyeccion_instancia("ALICE", "u103", default=default) == default


def test_gate_has_no_embedded_postgres_credential() -> None:
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert "postgresql://seal:" not in source
