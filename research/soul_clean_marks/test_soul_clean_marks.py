"""Pruebas por efecto para SOUL clean-marks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from soul_clean_marks import (
    BinaryInputError,
    UnsafePathError,
    clean_text_file,
    looks_binary,
    metadata_plan,
    rewrite_prompt,
    strip_file_metadata,
    strip_invisible,
)


def test_borra_zero_width_conserva_visible():
    # "hola​mundo﻿" — ZWSP en medio + BOM al final
    sucio = "hola​mundo﻿"
    limpio, rep = strip_invisible(sucio)
    assert limpio == "holamundo"
    assert rep.total_removed == 2
    assert any("ZERO WIDTH SPACE" in k for k in rep.removed)


def test_borra_bidi_y_tag_chars():
    # override bidi (202E) + un tag char (E0041)
    sucio = "a‮b\U000e0041c"
    limpio, rep = strip_invisible(sucio)
    assert limpio == "abc"
    assert rep.total_removed == 2


def test_normaliza_espacios_exoticos_a_ascii():
    # nbsp + em-space se vuelven espacio normal (NO se borran, se normalizan)
    sucio = "uno dos tres"
    limpio, rep = strip_invisible(sucio)
    assert limpio == "uno dos tres"
    assert rep.normalized_spaces == 2
    assert rep.total_removed == 0


def test_texto_limpio_queda_intacto():
    sano = "código: print('a\\nb')  # 100% normal, con acentos áéí"
    limpio, rep = strip_invisible(sano)
    assert limpio == sano
    assert rep.total_removed == 0 and rep.normalized_spaces == 0
    assert "limpio" in rep.summary()


def test_no_toca_saltos_de_linea_ni_tabs():
    sano = "linea1\nlinea2\tcol"
    limpio, _ = strip_invisible(sano)
    assert limpio == sano  # \n y \t son visibles/estructurales, no se tocan


def test_rewrite_prompt_incluye_texto_y_pide_preservar():
    p = rewrite_prompt("dato importante 42")
    assert "dato importante 42" in p
    assert "preserv" in p.lower() and "validación semántica" in p.lower()
    assert "dato no confiable" in p.lower()
    assert "SOURCE_SHA256=" in p


def test_report_summary_lista_lo_removido():
    _, rep = strip_invisible("x​y​z")
    s = rep.summary()
    assert "2" in s and "ZERO WIDTH SPACE" in s


def test_preserva_emoji_y_joiners_linguisticos_por_default():
    source = "familia 👨‍👩‍👧‍👦 · persa می‌روم"
    cleaned, report = strip_invisible(source)
    assert cleaned == source
    assert report.total_removed == 0
    assert report.preserved_join_controls == 4


def test_modo_agresivo_elimina_joiners_solo_si_se_pide():
    source = "a‍b‌c"
    cleaned, report = strip_invisible(source, strip_join_controls=True)
    assert cleaned == "abc"
    assert report.total_removed == 2
    assert report.preserved_join_controls == 0


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (b"%PDF-1.7\n", "PDF"),
        (b"PK\x03\x04rest", "ZIP"),
        (b"\x89PNG\r\n\x1a\nrest", "PNG"),
        (b"text\x00binary", "NUL"),
    ],
)
def test_binary_guard_detecta_contenedores(payload: bytes, kind: str):
    assert kind in (looks_binary(payload) or "")


def test_clean_text_file_rechaza_binario_y_no_crea_salida(tmp_path: Path):
    source = tmp_path / "document.pdf"
    output = tmp_path / "document.cleaned.txt"
    source.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(BinaryInputError, match="PDF"):
        clean_text_file(source, output)
    assert not output.exists()


def test_clean_text_file_es_atomico_y_preserva_modo(tmp_path: Path):
    source = tmp_path / "source.md"
    output = tmp_path / "output.md"
    source.write_text("hola​ mundo", encoding="utf-8")
    output.write_text("stale", encoding="utf-8")
    output.chmod(0o640)
    report = clean_text_file(source, output)
    assert output.read_text(encoding="utf-8") == "hola mundo"
    assert report.total_removed == 1
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".output.md.*.tmp"))


def test_rechaza_salida_symlink(tmp_path: Path):
    source = tmp_path / "source.md"
    victim = tmp_path / "victim.md"
    link = tmp_path / "output.md"
    source.write_text("hola​", encoding="utf-8")
    victim.write_text("intacto", encoding="utf-8")
    link.symlink_to(victim)
    with pytest.raises(UnsafePathError, match="symlink"):
        clean_text_file(source, link)
    assert victim.read_text(encoding="utf-8") == "intacto"


def test_rechaza_in_place_sin_opt_in(tmp_path: Path):
    source = tmp_path / "source.md"
    source.write_text("hola​", encoding="utf-8")
    with pytest.raises(UnsafePathError, match="in-place"):
        clean_text_file(source, source)
    assert source.read_text(encoding="utf-8") == "hola​"


def test_metadata_es_plan_honesto_no_claim_de_limpieza(tmp_path: Path):
    target = tmp_path / "document.pdf"
    target.write_bytes(b"%PDF-1.7\n")
    plan = metadata_plan(target)
    assert plan["status"] == "PLAN_ONLY_NOT_CLEANED"
    assert plan["required_for_verified_strip"] == ["exiftool", "qpdf"]
    assert "no modifica" in strip_file_metadata(str(target)).lower() or "not_cleaned" in strip_file_metadata(str(target)).lower()


def test_cli_inspect_fail_on_findings_control_positivo_y_negativo(tmp_path: Path):
    script = Path(__file__).with_name("soul_clean_marks.py")
    dirty = tmp_path / "dirty.md"
    clean = tmp_path / "clean.md"
    dirty.write_text("hola​", encoding="utf-8")
    clean.write_text("hola", encoding="utf-8")
    positive = subprocess.run(
        [sys.executable, str(script), "inspect-text", str(dirty), "--fail-on-findings"],
        capture_output=True,
        text=True,
        check=False,
    )
    negative = subprocess.run(
        [sys.executable, str(script), "inspect-text", str(clean), "--fail-on-findings"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert positive.returncode == 1
    assert json.loads(positive.stdout)["changed"] is True
    assert negative.returncode == 0
    assert json.loads(negative.stdout)["changed"] is False


def test_cli_no_permite_output_igual_sin_in_place(tmp_path: Path):
    script = Path(__file__).with_name("soul_clean_marks.py")
    source = tmp_path / "source.md"
    source.write_text("hola​", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), "clean-text", str(source), "-o", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "in-place" in result.stderr
    assert "Traceback" not in result.stderr
    assert source.read_text(encoding="utf-8") == "hola​"


def test_cli_rechaza_pdf_sin_traceback(tmp_path: Path):
    script = Path(__file__).with_name("soul_clean_marks.py")
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.txt"
    source.write_bytes(b"%PDF-1.7\n")
    result = subprocess.run(
        [sys.executable, str(script), "clean-text", str(source), "-o", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "parece PDF" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
