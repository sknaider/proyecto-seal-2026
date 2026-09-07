"""Controles adversariales de la integración multi-formato SOUL."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import zlib
import zipfile
from pathlib import Path

import pytest

import soul_full_clean
from soul_full_clean import (
    DEFAULT_UPSTREAM,
    UpstreamIntegrityError,
    VerificationError,
    _copy_locked_backend,
    _safe_environment,
    capabilities,
    clean_authorized_file,
    verify_upstream,
)
from soul_clean_marks import UnsafePathError


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind)
    crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _marked_png() -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00")
    return (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"caBX", b"c2pa manifest")
        + _png_chunk(b"tEXt", b"generator\x00Anthropic Claude")
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _docx_with_dangling_customxml_risk(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/customXml/item1.xml" ContentType="application/xml"/>'
            "</Types>",
        )
        archive.writestr("word/document.xml", '<w:document xmlns:w="urn:test"/>')
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml" Target="../customXml/item1.xml"/>'
            "</Relationships>",
        )
        archive.writestr("customXml/item1.xml", "<root>Anthropic Claude</root>")


def test_upstream_real_esta_ligado_a_commit_y_bytes():
    evidence = verify_upstream()
    assert evidence.commit == "61440192af4ef42d2f87280f238904c8456566d5"
    assert evidence.tree_sha256 == "308e60eadf93254ec33c858eefeaf2cd903703088e71879a6b466ef0d334f138"
    assert evidence.license == "MIT"


def test_tamper_de_un_byte_en_upstream_falla_cerrado(tmp_path: Path):
    copy = tmp_path / "upstream"
    shutil.copytree(DEFAULT_UPSTREAM, copy, ignore=shutil.ignore_patterns(".git"))
    target = copy / "skills/remove-ai-marks/scripts/clean_text.py"
    target.write_bytes(target.read_bytes() + b"\n# tamper\n")
    with pytest.raises(UpstreamIntegrityError, match="hash"):
        verify_upstream(copy)


def test_copia_efimera_no_arrastra_bytecode_ni_archivos_extra(tmp_path: Path):
    scripts = _copy_locked_backend(DEFAULT_UPSTREAM, tmp_path / "isolated")
    assert len(list(scripts.glob("*.py"))) == 19
    assert not list(scripts.rglob("*.pyc"))
    assert not list(scripts.rglob("__pycache__"))


def test_capabilities_no_promete_remocion_universal():
    report = capabilities()
    assert report["status"] == "PINNED_BACKEND_READY"
    assert report["universal_removal"] is False
    assert "docx/odt" in report["deterministic_formats"]


def test_no_propaga_secretos_del_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")
    child = _safe_environment()
    assert "ANTHROPIC_API_KEY" not in child
    assert "GITHUB_TOKEN" not in child
    assert child["PYTHONNOUSERSITE"] == "1"


def test_requiere_contenido_propio_o_autorizado(tmp_path: Path):
    source = tmp_path / "source.txt"
    output = tmp_path / "output.txt"
    source.write_text("hola\u200b", encoding="utf-8")
    with pytest.raises(PermissionError, match="propio o autorizado"):
        clean_authorized_file(source, output, authorized_content=False)
    assert not output.exists()


def test_texto_end_to_end_limpia_y_liga_evidencia(tmp_path: Path):
    source = tmp_path / "source.txt"
    output = tmp_path / "output.txt"
    source.write_text("hola\u200bmundo\u00ad\u3000SOUL", encoding="utf-8")
    source.chmod(0o640)
    evidence = clean_authorized_file(source, output, authorized_content=True)
    assert evidence.status == "VERIFIED_SUPPORTED_SIGNALS_CLEAN"
    assert output.read_text(encoding="utf-8") == "holamundo SOUL"
    assert evidence.before["suspicious_total"] == 3
    assert evidence.after["suspicious_total"] == 0
    assert evidence.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    if os.name == "posix":
        assert stat.S_IMODE(output.stat().st_mode) == 0o640


def test_html_end_to_end_conserva_contenido_visible(tmp_path: Path):
    source = tmp_path / "source.html"
    output = tmp_path / "output.html"
    source.write_text(
        '<html><head><meta name="generator" content="Claude">'
        '<meta name="viewport" content="width=device-width"></head>'
        '<body><p>SOUL visible</p></body></html>',
        encoding="utf-8",
    )
    evidence = clean_authorized_file(source, output, authorized_content=True)
    cleaned = output.read_text(encoding="utf-8")
    assert evidence.status == "VERIFIED_SUPPORTED_SIGNALS_CLEAN"
    assert "Claude" not in cleaned
    assert "viewport" in cleaned
    assert "SOUL visible" in cleaned


def test_png_end_to_end_remueve_c2pa_y_metadata_ai(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "output.png"
    source.write_bytes(_marked_png())
    evidence = clean_authorized_file(source, output, authorized_content=True)
    assert evidence.status == "VERIFIED_SUPPORTED_SIGNALS_CLEAN"
    assert evidence.before["has_c2pa"] is True
    assert evidence.after["has_c2pa"] is False
    assert evidence.after["has_ai_metadata"] is False
    assert b"caBX" not in output.read_bytes()
    assert b"Anthropic" not in output.read_bytes()


def test_docx_repara_relacion_que_upstream_dejaba_colgando(tmp_path: Path):
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _docx_with_dangling_customxml_risk(source)
    evidence = clean_authorized_file(source, output, authorized_content=True)
    assert evidence.status == "VERIFIED_SUPPORTED_SIGNALS_CLEAN"
    assert any(
        "dangling relationships" in action
        for action in evidence.cleaner["soul_post_actions"]
    )
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert "customXml/item1.xml" not in archive.namelist()
        relationships = archive.read("word/_rels/document.xml.rels")
        assert b"customXml/item1.xml" not in relationships


def test_postcondicion_roja_no_promueve_candidato(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source.txt"
    output = tmp_path / "output.txt"
    source.write_text("hola\u200b", encoding="utf-8")
    monkeypatch.setattr(soul_full_clean, "_residual_signals", lambda _report: ["cebo"])
    with pytest.raises(VerificationError, match="no se promovió"):
        clean_authorized_file(source, output, authorized_content=True)
    assert not output.exists()


def test_no_permite_in_place_ni_salida_symlink(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("hola\u200b", encoding="utf-8")
    with pytest.raises(UnsafePathError, match="in-place"):
        clean_authorized_file(source, source, authorized_content=True)

    victim = tmp_path / "victim.txt"
    output = tmp_path / "output.txt"
    victim.write_text("intacto", encoding="utf-8")
    output.symlink_to(victim)
    with pytest.raises(UnsafePathError, match="symlink"):
        clean_authorized_file(source, output, authorized_content=True)
    assert victim.read_text(encoding="utf-8") == "intacto"


def test_rechaza_entrada_symlink(tmp_path: Path):
    actual = tmp_path / "actual.txt"
    source = tmp_path / "source.txt"
    output = tmp_path / "output.txt"
    actual.write_text("hola\u200b", encoding="utf-8")
    source.symlink_to(actual)
    with pytest.raises(UnsafePathError, match="entrada symlink"):
        clean_authorized_file(source, output, authorized_content=True)
    assert not output.exists()


def test_cli_escribe_reporte_ligado_a_salida(tmp_path: Path):
    script = Path(soul_full_clean.__file__)
    source = tmp_path / "source.md"
    output = tmp_path / "output.md"
    report = tmp_path / "evidence.json"
    source.write_text("---\ngenerator: Claude\n---\nSOUL\u200b\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "clean",
            str(source),
            "-o",
            str(output),
            "--authorized-content",
            "--report",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "VERIFIED_SUPPORTED_SIGNALS_CLEAN"
    assert payload["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(result.stdout) == payload
