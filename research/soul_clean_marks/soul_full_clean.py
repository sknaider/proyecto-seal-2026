#!/usr/bin/env python3
"""Integración SOUL fail-closed del pipeline multi-formato watermarks-remover.

El upstream se usa únicamente para contenido propio o autorizado. Sus bytes se
verifican contra ``UPSTREAM.lock.json`` y se ejecutan desde una copia efímera;
el checkout mutable nunca es el proceso de confianza. La salida se genera fuera
del original, se reinspecciona y solo entonces se promueve atómicamente.

Esto prueba la remoción de señales que los inspectores soportan. No prueba que
un detector secreto de un proveedor falle ni que un texto sea humano.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from soul_clean_marks import MAX_TEXT_BYTES, UnsafePathError, atomic_write_bytes

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "UPSTREAM.lock.json"
DEFAULT_UPSTREAM = HERE.parent / "watermarks-remover"
MAX_FILE_BYTES = int(os.environ.get("SOUL_FULL_CLEAN_MAX_BYTES", str(256 << 20)))
SUBPROCESS_TIMEOUT = int(os.environ.get("SOUL_FULL_CLEAN_TIMEOUT", "180"))


class UpstreamIntegrityError(RuntimeError):
    """Los bytes del backend no coinciden con el lock aprobado."""


class VerificationError(RuntimeError):
    """La salida no satisface las postcondiciones verificables."""


@dataclass(frozen=True)
class UpstreamEvidence:
    repository: str
    commit: str
    license: str
    tree_sha256: str
    git_head: str | None
    isolated_copy: bool = True


@dataclass(frozen=True)
class CleanEvidence:
    status: str
    input: str
    output: str
    input_sha256: str
    output_sha256: str
    bytes_in: int
    bytes_out: int
    upstream: dict[str, Any]
    before: dict[str, Any]
    cleaner: dict[str, Any]
    after: dict[str, Any]
    residual_signals: list[str]
    limits: list[str]


def _load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "repository",
        "commit",
        "license",
        "scripts_and_license_sha256",
        "scripts_count",
    }
    missing = sorted(required - data.keys())
    if missing:
        raise UpstreamIntegrityError(f"lock incompleto: {', '.join(missing)}")
    return data


def _locked_paths(root: Path) -> list[Path]:
    scripts = sorted((root / "skills/remove-ai-marks/scripts").glob("*.py"))
    return sorted([*scripts, root / "LICENSE"])


def _tree_hash(root: Path) -> tuple[str, int]:
    paths = _locked_paths(root)
    if not paths or not all(path.is_file() and not path.is_symlink() for path in paths):
        raise UpstreamIntegrityError("backend incompleto o contiene symlinks")
    digest = hashlib.sha256()
    for path in paths:
        rel = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest(), len(paths) - 1


def _git_head(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def verify_upstream(root: str | Path = DEFAULT_UPSTREAM) -> UpstreamEvidence:
    """Liga repositorio, commit y bytes ejecutables al lock aprobado."""

    source = Path(root).resolve()
    lock = _load_lock()
    tree_hash, scripts_count = _tree_hash(source)
    if scripts_count != int(lock["scripts_count"]):
        raise UpstreamIntegrityError(
            f"cantidad de scripts distinta: {scripts_count} != {lock['scripts_count']}"
        )
    if tree_hash != lock["scripts_and_license_sha256"]:
        raise UpstreamIntegrityError(
            "hash del backend distinto al lock: "
            f"{tree_hash} != {lock['scripts_and_license_sha256']}"
        )
    head = _git_head(source)
    if head is not None and head != lock["commit"]:
        raise UpstreamIntegrityError(f"commit distinto al lock: {head} != {lock['commit']}")
    return UpstreamEvidence(
        repository=str(lock["repository"]),
        commit=str(lock["commit"]),
        license=str(lock["license"]),
        tree_sha256=tree_hash,
        git_head=head,
    )


def _copy_locked_backend(source: Path, destination: Path) -> Path:
    scripts_dst = destination / "skills/remove-ai-marks/scripts"
    scripts_dst.parent.mkdir(parents=True, exist_ok=True)
    scripts_dst.mkdir()
    for source_file in sorted((source / "skills/remove-ai-marks/scripts").glob("*.py")):
        shutil.copy2(source_file, scripts_dst / source_file.name)
    shutil.copy2(source / "LICENSE", destination / "LICENSE")
    expected = _load_lock()["scripts_and_license_sha256"]
    copied, _ = _tree_hash(destination)
    if copied != expected:
        raise UpstreamIntegrityError("la copia efímera no coincide con los bytes fijados")
    return scripts_dst


def _safe_environment() -> dict[str, str]:
    """No propaga tokens/credenciales al backend determinista."""

    return {
        "PATH": os.environ.get(
            "SOUL_FULL_CLEAN_TRUSTED_PATH",
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8:backslashreplace",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "WATERMARKS_MAX_INPUT_BYTES": str(MAX_FILE_BYTES),
        "WATERMARKS_MAX_STDIN_BYTES": str(MAX_TEXT_BYTES),
    }


def _run_json(
    command: list[str], *, timeout: int = SUBPROCESS_TIMEOUT
) -> tuple[int, dict[str, Any], str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_safe_environment(),
        start_new_session=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"backend no devolvió JSON válido (rc={result.returncode}): "
            f"{result.stderr[-500:]}"
        ) from exc
    return result.returncode, payload, result.stderr


def _python_command(script: Path, *arguments: str) -> list[str]:
    """Ejecuta el source fijado sin user-site, PYTHONPATH ni bytecode cacheado."""

    return [sys.executable, "-I", "-B", str(script), *arguments]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relationship_owner_dir(rel_path: str) -> str:
    """Directorio del part dueño de un archivo OOXML ``.rels``."""

    if rel_path == "_rels/.rels":
        return ""
    marker = "/_rels/"
    if marker not in rel_path or not rel_path.endswith(".rels"):
        return ""
    prefix, rel_name = rel_path.split(marker, 1)
    owner_name = rel_name[: -len(".rels")]
    return posixpath.dirname(posixpath.join(prefix, owner_name))


def _resolve_ooxml_target(rel_path: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(_relationship_owner_dir(rel_path), target))


def _repair_docx_relationships(data: bytes) -> tuple[bytes, list[str]]:
    """Quita relaciones/overrides que apuntan a parts eliminados por el upstream."""

    actions: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        if source.testzip() is not None:
            raise VerificationError("DOCX candidato tiene CRC inválido")
        names = set(source.namelist())
        rewritten: dict[str, bytes] = {}
        for info in source.infolist():
            raw = source.read(info.filename)
            if info.filename.endswith(".rels"):
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError as exc:
                    raise VerificationError(
                        f"DOCX .rels inválido: {info.filename}: {exc}"
                    ) from exc
                removed = 0
                for relation in list(root):
                    if relation.attrib.get("TargetMode", "").lower() == "external":
                        continue
                    target = relation.attrib.get("Target")
                    if not target:
                        continue
                    resolved = _resolve_ooxml_target(info.filename, target)
                    if resolved.startswith("../") or resolved not in names:
                        root.remove(relation)
                        removed += 1
                if removed:
                    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                    actions.append(
                        f"drop dangling relationships {info.filename} x{removed}"
                    )
            elif info.filename == "[Content_Types].xml":
                try:
                    root = ET.fromstring(raw)
                except ET.ParseError as exc:
                    raise VerificationError(f"DOCX Content_Types inválido: {exc}") from exc
                removed = 0
                for child in list(root):
                    part = child.attrib.get("PartName")
                    if part and part.lstrip("/") not in names:
                        root.remove(child)
                        removed += 1
                if removed:
                    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                    actions.append(f"drop dangling Content_Types overrides x{removed}")
            rewritten[info.filename] = raw

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as destination:
            for info in source.infolist():
                destination.writestr(info, rewritten[info.filename])
    repaired = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(repaired)) as verified:
        if verified.testzip() is not None:
            raise VerificationError("DOCX reparado no supera CRC")
    return repaired, actions


def _postprocess_candidate(candidate: Path) -> list[str]:
    if candidate.suffix.lower() != ".docx":
        return []
    repaired, actions = _repair_docx_relationships(candidate.read_bytes())
    if actions:
        atomic_write_bytes(candidate, repaired)
    return actions


def _residual_signals(report: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    if int(report.get("suspicious_total", 0)):
        signals.append(f"unicode_suspicious={report['suspicious_total']}")
    if report.get("has_c2pa"):
        signals.append("c2pa")
    if report.get("has_ai_metadata"):
        signals.append("ai_metadata")
    for finding in report.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("confidence", "")) in {"confirmed", "probable"}:
            signals.append(str(finding.get("finding") or finding))
    return signals


def _validate_paths(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise UnsafePathError(f"entrada debe ser archivo regular, no symlink: {source}")
    if destination.is_symlink():
        raise UnsafePathError(f"salida symlink rechazada: {destination}")
    if source.resolve() == destination.resolve():
        raise UnsafePathError("la integración completa no permite in-place; use otra salida")
    if not destination.parent.is_dir():
        raise UnsafePathError(f"directorio de salida inexistente: {destination.parent}")
    size = source.stat().st_size
    if size > MAX_FILE_BYTES:
        raise VerificationError(f"entrada demasiado grande: {size} > {MAX_FILE_BYTES}")


def inspect_file(
    path: str | Path, *, upstream_root: str | Path = DEFAULT_UPSTREAM
) -> dict[str, Any]:
    raw_source = Path(path)
    if raw_source.is_symlink():
        raise UnsafePathError(f"entrada symlink rechazada: {raw_source}")
    source = raw_source.resolve()
    if not source.is_file():
        raise UnsafePathError(f"entrada debe ser archivo regular: {source}")
    verify_upstream(upstream_root)
    with tempfile.TemporaryDirectory(prefix="soul-clean-inspect-") as temp:
        scripts = _copy_locked_backend(Path(upstream_root).resolve(), Path(temp))
        rc, report, stderr = _run_json(
            _python_command(scripts / "inspect_file.py", "--json", str(source))
        )
        if rc not in (0, 1):
            raise VerificationError(f"inspect falló rc={rc}: {stderr[-500:]}")
        return report


def clean_authorized_file(
    source: str | Path,
    destination: str | Path,
    *,
    authorized_content: bool,
    upstream_root: str | Path = DEFAULT_UPSTREAM,
    allow_residual: bool = False,
) -> CleanEvidence:
    """Limpia un archivo autorizado con pin, aislamiento y verificación doble."""

    if not authorized_content:
        raise PermissionError(
            "se requiere confirmación explícita de que el contenido es propio o autorizado"
        )
    raw_source = Path(source)
    if raw_source.is_symlink():
        raise UnsafePathError(f"entrada symlink rechazada: {raw_source}")
    source_path = raw_source.resolve()
    destination_path = Path(destination).absolute()
    _validate_paths(source_path, destination_path)
    upstream = verify_upstream(upstream_root)
    input_hash = _sha256(source_path)
    input_mode = source_path.stat().st_mode & 0o7777

    with tempfile.TemporaryDirectory(prefix="soul-full-clean-") as temp:
        work = Path(temp)
        scripts = _copy_locked_backend(Path(upstream_root).resolve(), work / "backend")
        candidate = work / f"candidate{source_path.suffix}"

        before_rc, before, before_err = _run_json(
            _python_command(scripts / "inspect_file.py", "--json", str(source_path))
        )
        if before_rc not in (0, 1):
            raise VerificationError(f"pre-inspección falló rc={before_rc}: {before_err[-500:]}")

        clean_rc, cleaner, clean_err = _run_json(
            _python_command(
                scripts / "clean_file.py",
                str(source_path),
                "-o",
                str(candidate),
                "--json",
            )
        )
        if clean_rc not in (0, 1) or not candidate.is_file() or candidate.is_symlink():
            raise VerificationError(f"limpieza falló rc={clean_rc}: {clean_err[-500:]}")

        soul_actions = _postprocess_candidate(candidate)
        if soul_actions:
            cleaner = dict(cleaner)
            cleaner["soul_post_actions"] = soul_actions

        after_rc, after, after_err = _run_json(
            _python_command(scripts / "inspect_file.py", "--json", str(candidate))
        )
        if after_rc not in (0, 1):
            raise VerificationError(f"post-inspección falló rc={after_rc}: {after_err[-500:]}")
        residual = _residual_signals(after)
        if residual and not allow_residual:
            raise VerificationError(
                "postcondición falló; la salida no se promovió: " + "; ".join(residual)
            )

        output_bytes = candidate.read_bytes()
        atomic_write_bytes(destination_path, output_bytes, mode=input_mode)
        output_hash = _sha256(destination_path)
        if output_hash != hashlib.sha256(output_bytes).hexdigest():
            raise VerificationError("los bytes promovidos no coinciden con el candidato verificado")

    status = "VERIFIED_SUPPORTED_SIGNALS_CLEAN" if not residual else "RESIDUAL_ALLOWED"
    return CleanEvidence(
        status=status,
        input=str(source_path),
        output=str(destination_path),
        input_sha256=input_hash,
        output_sha256=output_hash,
        bytes_in=source_path.stat().st_size,
        bytes_out=destination_path.stat().st_size,
        upstream=asdict(upstream),
        before=before,
        cleaner=cleaner,
        after=after,
        residual_signals=residual,
        limits=[
            "No certifica detectores secretos o claves privadas de proveedores.",
            "No prueba autoría humana.",
            "Marcas estadísticas requieren reescritura y verificación por esquema.",
            "Marcas de píxel/audio/video y C2PA soft-binding pueden sobrevivir.",
        ],
    )


def capabilities(upstream_root: str | Path = DEFAULT_UPSTREAM) -> dict[str, Any]:
    upstream = verify_upstream(upstream_root)
    return {
        "status": "PINNED_BACKEND_READY",
        "upstream": asdict(upstream),
        "deterministic_formats": [
            "txt/code",
            "md/html",
            "svg",
            "png/jpeg/webp",
            "docx/odt",
            "pdf-best-effort",
        ],
        "optional_tools": {
            name: bool(shutil.which(name)) for name in ("exiftool", "c2patool", "qpdf")
        },
        "external_backends": {
            "markdiffusion": {
                "license": "Apache-2.0",
                "shipping": "optional_not_bundled",
                "verification": "same-scheme/same-model only",
            },
            "reverse_synthid": {
                "license": "non-commercial research",
                "shipping": "excluded_from_SOUL_commercial_product",
            },
            "ctrlregen_noai_watermark": {
                "license": "missing/all-rights-reserved",
                "shipping": "excluded_from_SOUL_product",
            },
        },
        "guarantee": "solo señales soportadas por la inspección antes/después",
        "universal_removal": False,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default=str(DEFAULT_UPSTREAM))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")

    inspect_cmd = commands.add_parser("inspect")
    inspect_cmd.add_argument("path")

    clean_cmd = commands.add_parser("clean")
    clean_cmd.add_argument("path")
    clean_cmd.add_argument("-o", "--output", required=True)
    clean_cmd.add_argument(
        "--authorized-content",
        action="store_true",
        help="confirma que el contenido es propio o está autorizado",
    )
    clean_cmd.add_argument(
        "--allow-residual",
        action="store_true",
        help="promueve aun con señales residuales, marcándolas en el reporte",
    )
    clean_cmd.add_argument("--report", help="escribir evidencia JSON atómicamente")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "capabilities":
            payload: Any = capabilities(args.upstream)
        elif args.command == "inspect":
            payload = inspect_file(args.path, upstream_root=args.upstream)
        else:
            payload = asdict(
                clean_authorized_file(
                    args.path,
                    args.output,
                    authorized_content=args.authorized_content,
                    upstream_root=args.upstream,
                    allow_residual=args.allow_residual,
                )
            )
            if args.report:
                atomic_write_bytes(
                    args.report,
                    (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (
        OSError,
        PermissionError,
        subprocess.SubprocessError,
        UnsafePathError,
        UpstreamIntegrityError,
        VerificationError,
    ) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
