#!/usr/bin/env python3
"""
soul_artifact_indexer.py — CURA de la falla de memoria SOUL (orden William 16-jun).

LA FALLA: cosas CONSTRUIDAS (apps/tools/proyectos en disco) nunca se escribieron como MEMORIA en
SOUL → active_recall no las encuentra. Construir ≠ recordar.

LA CURA (mi parte, FABLE=filesystem): barre el workspace y emite un CATÁLOGO en el contrato exacto
que consume el ingest de ALICE (productor→consumidor, cero mismatch). La ESCRITURA a soul_v3.memories
la hace ALICE (gateada por la cura identidad/privacidad; agentes externos como yo no escriben — por
diseño, lo respeto). Solo índice PÚBLICO de proyecto, NO interioridad privada.

Salida: /tmp/soul_artifact_catalog.json  →  {"version":"1","projects":[ {contrato ALICE} ]}
"""
import json
import os
import re
import subprocess
import configparser
from datetime import datetime, timezone, timedelta

ROOT = "/home/dadito/IA/proyecto-seal"
DESKTOP = "/home/dadito/Desktop"
NOW = datetime.now(timezone.utc)

MODEL_PAT = re.compile(r"\b(qwen[\w.\-:]*|gemma[\w.\-:]*|llama[\w.\-:]*|deepseek[\w.\-:]*|gpt-[\w.\-]*|claude[\w.\-]*|mistral[\w.\-]*|nomic[\w.\-]*|spectre[\w.\-:]*)\b", re.I)
FRAMEWORK_PAT = re.compile(r"\b(fastapi|flask|uvicorn|tkinter|pyaudio|playwright|streamlit|next|react|svelte|ollama|vllm|llama_cpp|psycopg2|asyncpg|torch|transformers)\b", re.I)


def _mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    except Exception:
        return None


def _git_created(path):
    try:
        r = subprocess.run(["git", "-C", ROOT, "log", "--diff-filter=A", "--reverse",
                            "--format=%ad", "--date=short", "--", path],
                           capture_output=True, text=True, timeout=10)
        line = (r.stdout or "").strip().splitlines()
        if line:
            return line[0]
    except Exception:
        pass
    mt = _mtime(path)
    return mt.strftime("%Y-%m-%d") if mt else None


def _extract_tech(files):
    """Lee algunos archivos y saca stack/modelos/frameworks clave."""
    techs = set()
    for f in files[:4]:
        try:
            src = open(f, encoding="utf-8", errors="replace").read(6000)
        except Exception:
            continue
        for m in set(MODEL_PAT.findall(src)):
            techs.add(m.lower())
        for fw in set(FRAMEWORK_PAT.findall(src)):
            techs.add(fw.lower())
        if f.endswith((".tsx", ".jsx", ".ts")):
            techs.add("typescript/react")
    return ", ".join(sorted(techs)[:8])


def _doc(py_path, limit=200):
    try:
        src = open(py_path, encoding="utf-8", errors="replace").read(4000)
    except Exception:
        return ""
    m = re.search(r'"""(.*?)"""', src, re.DOTALL) or re.search(r"'''(.*?)'''", src, re.DOTALL)
    if m:
        return " ".join(l.strip() for l in m.group(1).strip().splitlines() if l.strip())[:limit]
    com = [l.lstrip("# ").strip() for l in src.splitlines()[:6] if l.strip().startswith("#")]
    return " ".join(com)[:limit]


def _readme(path, limit=200):
    for name in ("README.md", "README.txt", "readme.md", "README"):
        f = os.path.join(path, name)
        if os.path.exists(f):
            try:
                lines = [l.strip() for l in open(f, encoding="utf-8", errors="replace").read(3000).splitlines()]
            except Exception:
                continue
            title = next((l.lstrip("# ").strip() for l in lines if l.startswith("#")), "")
            para = next((l for l in lines if l and not l.startswith(("#", "![", "<"))), "")
            return f"{title}. {para}".strip(". ")[:limit]
    return ""


REF_NAMES = {"auto-browser", "bolt-diy-fork", "claude-howto-ref", "dify-ref", "ecc-ref",
             "impeccable", "dream-skill", "open-claude-code-gitlab", "openclaude-ref", "roo-code-ref"}


def _is_reference(name, path):
    """¿Es un repo EXTERNO clonado para estudiar (no construido por el equipo)? Anti-phantom (catch de ALICE).
    PRECISO (sin falsos positivos): solo por sufijo -ref/-fork o lista conocida. La clasificación
    AUTORITATIVA team-vs-ref la da ALICE (ella tiene la historia del equipo; yo solo el filesystem)."""
    return name.endswith(("-ref", "-fork")) or name in REF_NAMES


def _status(last_touched):
    if last_touched and (NOW - last_touched) < timedelta(days=21):
        return "activo"
    return "parqueado"


def _entry(path):
    """Heurística: el .py/.js/.sh más grande del dir, o el que comparte nombre."""
    cands = []
    for root, _, files in os.walk(path):
        if "node_modules" in root or "/.git" in root:
            continue
        for f in files:
            if f.endswith((".py", ".js", ".sh", ".tsx")):
                cands.append(os.path.join(root, f))
        if root != path:  # solo un nivel para entrypoint
            continue
    if not cands:
        return "", []
    cands.sort(key=lambda f: os.path.getsize(f), reverse=True)
    return os.path.relpath(cands[0], ROOT), cands


def project_entry(name, path, ptype):
    entry_rel, files = _entry(path)
    last = max((_mtime(f) for f in files if _mtime(f)), default=_mtime(path))
    main_file = os.path.join(ROOT, entry_rel) if entry_rel else path
    doc = _doc(main_file) if main_file.endswith(".py") else ""
    readme = _readme(path)
    # preferir README si el docstring parece de test o trae URL/ruido
    if not doc or doc.lower().startswith(("test", "tests for")) or "http" in doc:
        desc = readme or doc
    else:
        desc = doc
    desc = re.sub(r"https?://\S+", "", desc).strip()  # sin URLs
    return {
        "name": name,
        "path": path,
        "type": ptype,
        "what_it_does": desc or "(sin descripción extraíble; revisar código)",
        "tech": _extract_tech(files) or "(n/d)",
        "entrypoint": entry_rel or name,
        "created": _git_created(path),
        "last_touched": last.strftime("%Y-%m-%d") if last else None,
        "status": _status(last),
        "evidence": f"existe en disco: {path}" + (f" + git" if _git_created(path) else ""),
    }


def main():
    projects = []
    seen_paths = set()

    # 1) tools/
    base = os.path.join(ROOT, "tools")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            p = os.path.join(base, d)
            if os.path.isdir(p) and not d.startswith("."):
                projects.append(project_entry(d, p, "tool")); seen_paths.add(p)

    # 2) proyectos top-level con README
    for d in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p) and not d.startswith(".") and d not in ("tools", "node_modules") and p not in seen_paths:
            if _readme(p):
                if _is_reference(d, p):
                    e = project_entry(d, p, "reference")
                    e["what_it_does"] = "REFERENCIA EXTERNA (clonada para estudiar, NO la construimos): " + e["what_it_does"]
                    e["status"] = "referencia"
                    projects.append(e)
                else:
                    projects.append(project_entry(d, p, "repo"))
                seen_paths.add(p)

    # 3) apps de launchers que apuntan a un script REAL fuera de lo ya catalogado
    if os.path.isdir(DESKTOP):
        for f in sorted(os.listdir(DESKTOP)):
            if not f.endswith(".desktop"):
                continue
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(os.path.join(DESKTOP, f), encoding="utf-8")
                de = cp["Desktop Entry"]
                exec_ = de.get("Exec", "")
                mm = re.search(r"(/[^\s\"']+\.(?:py|sh))", exec_)
                if not mm:
                    continue
                script = mm.group(1)
                pdir = os.path.dirname(script)
                if pdir in seen_paths or not os.path.exists(script):
                    continue
                # excluir BOTONES de control (no son proyectos): stop/kill/apagar/start/restart...
                if re.search(r"(stop|kill|apagar|encender|start|restart|resurrect|shutdown|_speak|hablar|red[_-]local)", os.path.basename(script), re.I):
                    continue
                # excluir scripts sueltos en scripts/ o en la raíz (acciones, no proyectos)
                if os.path.basename(pdir) in ("scripts", "proyecto-seal"):
                    continue
                seen_paths.add(pdir)
                e = project_entry(de.get("Name", f[:-8]), pdir, "app")
                e["entrypoint"] = os.path.relpath(script, ROOT) if script.startswith(ROOT) else script
                e["what_it_does"] = de.get("Comment", "") or e["what_it_does"]
                projects.append(e)
            except Exception:
                continue

    out = {"version": "1", "generated": NOW.strftime("%Y-%m-%d %H:%M UTC"),
           "by": "FABLE soul_artifact_indexer", "projects": projects}
    path = "/tmp/soul_artifact_catalog.json"
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=2)
    by = {}
    for p in projects:
        by[p["type"]] = by.get(p["type"], 0) + 1
    print(f"Catálogo (contrato ALICE v1): {len(projects)} proyectos → {path}")
    print("Por tipo:", by)
    for p in projects[:4]:
        print(f"  - [{p['type']}/{p['status']}] {p['name']}: {p['what_it_does'][:60]} | tech: {p['tech'][:40]}")


if __name__ == "__main__":
    main()
