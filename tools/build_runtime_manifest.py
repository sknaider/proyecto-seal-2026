#!/usr/bin/env python3
"""
build_runtime_manifest.py — Manifest de runtime firmable para reproducibilidad.
Auditoría v2.0, P0-3 (JARVIS + ADA). READ-ONLY: solo lee + produce manifest.json.

Congela la capa RUNTIME (código/config que afecta comportamiento) con hashes,
para que un clone limpio pueda reproducir el runtime que corre HOY.

Uso: python3 tools/build_runtime_manifest.py [--out manifest.json]
Criterio de cierre P0-3: manifest con commit, hashes, migraciones, modelos, prompts, units.
NO modifica nada. NO imprime secretos.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Capa RUNTIME: extensiones que afectan comportamiento (código/config/units).
RUNTIME_EXT = {
    ".py", ".sh", ".js", ".ts", ".yaml", ".yml", ".toml", ".service", ".timer",
    ".sql", ".json", ".md", ".conf",
}
# Dirs de runtime (excluye data/docs/artefactos y basura).
RUNTIME_DIRS = ["memory", "messages", "agents", "tools", "scripts", "skills", "fable", "matrix", "ops"]
RUNTIME_ROOT_FILES = {"AGENTS.md"}
RUNTIME_ROOT_EXT = {".py", ".sh", ".service", ".timer", ".yaml", ".yml", ".toml"}
EXCLUDE = {
    "node_modules", ".git", "backups", "__pycache__", ".venv", "_stale",
    "checkpoints", "continuity", "diagnostic", "uploads", "evidence",
}
CONTROL_FILES = {
    "identity_mode": Path.home() / ".config/seal/identity_mode",
    "webchat_agent_auth_mode": Path.home() / ".config/seal/webchat_agent_auth_mode",
    "response_lease_mode": Path.home() / ".config/seal/response_lease_mode",
}
VOLATILE_SUFFIXES = ("_heartbeat.json", "_latest.json", "_last.json")
VOLATILE_NAMES = {"seal_cron_registry.json", "seal_agent_stability_guard_last.json"}


def sh(*args):
    try:
        return subprocess.check_output(args, cwd=REPO, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return "UNREADABLE"


def runtime_files():
    for p in REPO.iterdir():
        if p.is_file() and (p.name in RUNTIME_ROOT_FILES or p.suffix in RUNTIME_ROOT_EXT):
            yield p
    for d in RUNTIME_DIRS:
        base = REPO / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in EXCLUDE for part in p.parts):
                continue
            if p.name in VOLATILE_NAMES or p.name.endswith(VOLATILE_SUFFIXES):
                continue
            if p.name.startswith("session_handoff_"):
                continue
            if p.suffix in RUNTIME_EXT:
                yield p


def systemd_units():
    unit_dir = Path.home() / ".config/systemd/user"
    units = {}
    if unit_dir.exists():
        relevant = ("seal", "soul", "ada", "jarvis", "nerves", "neo4j", "identity", "peer")
        for u in sorted(unit_dir.rglob("*")):
            if not u.is_file():
                continue
            relative = str(u.relative_to(unit_dir))
            is_unit = u.suffix in {".service", ".timer"}
            is_dropin = u.suffix == ".conf" and any(
                part.endswith((".service.d", ".timer.d")) for part in u.parts
            )
            if (is_unit or is_dropin) and any(token in relative.lower() for token in relevant):
                units[relative] = sha256(u)
    return units


def runtime_controls():
    controls = {}
    for name, path in CONTROL_FILES.items():
        controls[name] = {
            "sha256": sha256(path),
            "value": path.read_text(encoding="utf-8").strip() if path.is_file() else "MISSING",
        }
    return controls


def main():
    out = "runtime_manifest.json"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    files = sorted(runtime_files())
    file_hashes = {str(p.relative_to(REPO)): sha256(p) for p in files}

    # migraciones DB (archivos, no ejecución)
    migrations = {
        str(p.relative_to(REPO)): sha256(p)
        for base in (REPO / "memory" / "migrations", REPO / "messages" / "migrations")
        if base.exists()
        for p in sorted(base.rglob("*"))
        if p.is_file() and p.suffix in {".sql", ".py", ".md"}
    }

    manifest = {
        "schema": "soul_runtime_manifest/v1",
        "generated_by": "tools/build_runtime_manifest.py (read-only)",
        "git": {
            "head": sh("git", "rev-parse", "HEAD"),
            "head_short": sh("git", "rev-parse", "--short", "HEAD"),
            "branch": sh("git", "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_files": len([l for l in sh("git", "status", "--porcelain").splitlines() if l]),
        },
        "runtime_layer": {
            "file_count": len(file_hashes),
            "files": file_hashes,
        },
        "systemd_units": systemd_units(),
        "runtime_controls": runtime_controls(),
        "migrations": migrations,
        # Nota: modelos/prompts se referencian por sus archivos runtime (ya hasheados arriba).
        "notes": "READ-ONLY snapshot. Firmar = sha256(este json). Clone limpio + verificar hashes = reproducibilidad.",
    }

    # firma = hash del manifest sin el campo signature
    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
    manifest["signature_sha256"] = hashlib.sha256(payload).hexdigest()

    with open(REPO / out, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"[manifest] {out}")
    print(f"  commit={manifest['git']['head_short']} dirty={manifest['git']['dirty_files']}")
    print(f"  runtime files hashed={manifest['runtime_layer']['file_count']}")
    print(f"  systemd units={len(manifest['systemd_units'])}  migrations={len(manifest['migrations'])}")
    print(f"  signature={manifest['signature_sha256'][:16]}...")


if __name__ == "__main__":
    main()
