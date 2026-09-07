#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
create_agent.py - PROTOTIPO del creador/personalizador de agente SOUL (device).
================================================================================
Crea y personaliza un agente sobre el alma LOCAL (soul_v3) del device, y lo deja
listo para que un Claude/Codex lo 'encienda'. Pedido de William (8-jul): "que sea
personalizable... dame un prototipo lo probamos y luego modificamos".

PERSONALIZABLE EN:
  1. Identidad ....... nombre + rol/descripcion
  2. Personalidad .... los 5 OCEAN (0.0-1.0) desde cero, o CLONANDO un agente base
  3. Memoria ......... a que accede: all | <agente> | own   (scope, lo respeta el MCP)
  4. Cerebro ......... claude | codex | local:<modelo LM Studio>   (informativo -> launcher)
  5. Reglas .......... instintos/reglas base (que siempre/nunca hace)

Provisiona por EFECTO:
  - INSERT/UPDATE en soul_v3.agents (nombre, rol, system_prompt, ocean_*, persona_axes)
  - genera .mcp.json (server de memoria soul_mcp.py, con AGENT_NAME + MEMORY_SCOPE)
  - genera un launcher/README

Modos:
  interactivo:  python create_agent.py
  por archivo:  python create_agent.py --config agente.json [--dry-run]
  clonar base:  (dentro del wizard o en el json: "base":"NEXUS")

Deps: asyncpg   ·   DATABASE_URL = ⟦SOUL:5bf6771a9d⟧ (default localhost)
"""
from __future__ import annotations
import os, sys, json, argparse, asyncio, re
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:REDACTADO@localhost:5432/seal_memory")
OUT_DIR = os.environ.get("AGENT_OUT_DIR", os.path.dirname(os.path.abspath(__file__)))
OCEAN_KEYS = ["ocean_o", "ocean_c", "ocean_e", "ocean_a", "ocean_n"]
OCEAN_LABEL = {"ocean_o": "Apertura", "ocean_c": "Responsabilidad", "ocean_e": "Extraversion",
               "ocean_a": "Amabilidad", "ocean_n": "Inestabilidad emocional"}
VALID_SCOPES_HINT = "all | <NombreAgente> | own"


def _describe_ocean(ocean: dict) -> str:
    """Traduce los 5 OCEAN a una descripcion en lenguaje natural (para que William 'vea' la personalidad)."""
    def band(v, low, high):
        return low if v < 0.4 else (high if v > 0.66 else "equilibrado en")
    parts = [
        f"{band(ocean['ocean_o'],'practico y concreto','muy curioso y abierto')} lo nuevo",
        f"{band(ocean['ocean_c'],'espontaneo','muy metodico y responsable')}",
        f"{band(ocean['ocean_e'],'reservado','muy expresivo y social')}",
        f"{band(ocean['ocean_a'],'directo y critico','muy amable y colaborador')}",
        f"{'sereno y estable' if ocean['ocean_n'] < 0.4 else ('reactivo emocionalmente' if ocean['ocean_n'] > 0.66 else 'emocionalmente equilibrado')}",
    ]
    return "; ".join(parts)


def _clamp01(x, default=0.5):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


async def _connect():
    return await asyncpg.connect(DATABASE_URL)


async def load_base(conn, base: str) -> dict:
    """Clona OCEAN + system_prompt de un agente existente como punto de partida."""
    row = await conn.fetchrow(
        "SELECT name, role, system_prompt, ocean_o,ocean_c,ocean_e,ocean_a,ocean_n "
        "FROM soul_v3.agents WHERE lower(name)=lower($1)", base)
    if not row:
        raise SystemExit(f"[create_agent] agente base '{base}' no existe en el alma local.")
    return dict(row)


async def name_taken(conn, name: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM soul_v3.agents WHERE lower(name)=lower($1)", name))


def validate(cfg: dict) -> list[str]:
    errs = []
    if not cfg.get("name", "").strip():
        errs.append("falta 'name'")
    if not re.match(r"^[A-Za-z0-9_\- ]{2,40}$", cfg.get("name", "")):
        errs.append("'name' debe ser 2-40 chars alfanumericos/_/-")
    for k in OCEAN_KEYS:
        v = cfg.get(k)
        if v is not None and not (0.0 <= float(v) <= 1.0):
            errs.append(f"{k} fuera de rango 0.0-1.0")
    scope = cfg.get("memory_access", "own")
    if scope not in ("all", "own") and not re.match(r"^[A-Za-z0-9_\-]{2,40}$", scope):
        errs.append(f"memory_access invalido ({VALID_SCOPES_HINT})")
    return errs


def _mcp_json(cfg: dict) -> dict:
    scope = cfg.get("memory_access", "own")
    # own -> el MCP filtra por el nombre del agente; all -> sin filtro; <agente> -> ese
    return {
        "mcpServers": {
            "soul-memory": {
                "command": "python",
                "args": [os.path.join(OUT_DIR, "soul_mcp.py")],
                "env": {
                    "DATABASE_URL": DATABASE_URL,
                    "AGENT_NAME": cfg["name"],
                    "MEMORY_SCOPE": scope,
                },
            }
        }
    }


async def provision(cfg: dict, dry_run: bool = False) -> dict:
    conn = await _connect()
    try:
        errs = validate(cfg)
        if errs:
            raise SystemExit("[create_agent] config invalida:\n  - " + "\n  - ".join(errs))
        if await name_taken(conn, cfg["name"]) and not cfg.get("overwrite"):
            raise SystemExit(f"[create_agent] ya existe un agente '{cfg['name']}'. Usa overwrite=true para reemplazar.")

        ocean = {k: _clamp01(cfg.get(k, 0.5)) for k in OCEAN_KEYS}
        persona = {
            "memory_access": cfg.get("memory_access", "own"),
            "model": cfg.get("model", "claude"),
            "rules": cfg.get("rules", []),
            "communication_style": cfg.get("communication_style", "directo"),
            "verbosity": cfg.get("verbosity", "balanceado"),
            "expertise": cfg.get("expertise", ""),
            "emotional_baseline": _clamp01(cfg.get("emotional_baseline", 0.6)),
            "created_by": "create_agent_prototype",
        }
        if not dry_run:
            await conn.execute("""
                INSERT INTO soul_v3.agents (name, role, system_prompt,
                    ocean_o, ocean_c, ocean_e, ocean_a, ocean_n, persona_axes, active, created_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, TRUE, NOW(), NOW())
                ON CONFLICT (name) DO UPDATE SET
                    role=EXCLUDED.role, system_prompt=EXCLUDED.system_prompt,
                    ocean_o=EXCLUDED.ocean_o, ocean_c=EXCLUDED.ocean_c, ocean_e=EXCLUDED.ocean_e,
                    ocean_a=EXCLUDED.ocean_a, ocean_n=EXCLUDED.ocean_n,
                    persona_axes=EXCLUDED.persona_axes, active=TRUE, updated_at=NOW()
            """, cfg["name"], cfg.get("role", ""), cfg.get("system_prompt", ""),
                ocean["ocean_o"], ocean["ocean_c"], ocean["ocean_e"], ocean["ocean_a"], ocean["ocean_n"],
                json.dumps(persona))

        mcp = _mcp_json(cfg)
        written = []
        if not dry_run:
            mcp_path = os.path.join(OUT_DIR, ".mcp.json")
            with open(mcp_path, "w", encoding="utf-8") as f:
                json.dump(mcp, f, indent=2, ensure_ascii=False)
            written.append(mcp_path)
            readme = os.path.join(OUT_DIR, f"AGENTE_{cfg['name']}.txt")
            with open(readme, "w", encoding="utf-8") as f:
                f.write(_summary(cfg, ocean, persona))
            written.append(readme)
        return {"ok": True, "agent": cfg["name"], "ocean": ocean, "persona": persona,
                "mcp": mcp, "written": written, "dry_run": dry_run}
    finally:
        await conn.close()


def _summary(cfg, ocean, persona) -> str:
    lines = [
        f"AGENTE CREADO: {cfg['name']}",
        f"  Rol: {cfg.get('role','')}",
        f"  En pocas palabras: {_describe_ocean(ocean)}.",
        "  Personalidad (OCEAN 0-1):",
    ]
    for k in OCEAN_KEYS:
        lines.append(f"    {OCEAN_LABEL[k]:24} {ocean[k]:.2f}")
    extra = persona.get("communication_style") or persona.get("verbosity") or persona.get("expertise")
    if extra:
        lines.append(f"  Estilo: {persona.get('communication_style','-')} / {persona.get('verbosity','-')}"
                     + (f" · experticia: {persona.get('expertise')}" if persona.get('expertise') else ""))
    lines += [
        f"  Acceso a memoria: {persona['memory_access']}",
        f"  Cerebro (modelo): {persona['model']}",
        f"  Reglas: {persona['rules']}",
        "",
        "COMO ENCENDERLO:",
        "  1) Instala Claude Code (necesita Node.js).",
        f"  2) Copia el .mcp.json de esta carpeta a la carpeta donde corres 'claude'.",
        "  3) Corre  claude  ahi. El agente ya tiene su personalidad + acceso a la memoria local.",
        "",
        "El system_prompt del agente:",
        "  " + (cfg.get("system_prompt", "") or "(vacio)"),
    ]
    return "\n".join(lines)


# ───────────────────────── wizard interactivo ─────────────────────────
def _ask(prompt, default=""):
    v = input(f"{prompt}" + (f" [{default}]" if default != "" else "") + ": ").strip()
    return v or default


async def wizard() -> dict:
    print("=== Creador de agente SOUL (prototipo) ===")
    cfg = {}
    cfg["name"] = _ask("Nombre del agente")
    cfg["role"] = _ask("Rol / descripcion corta", "Asistente personal")
    base = _ask("Basar personalidad en un agente existente? (NEXUS/JARVIS/ADA/ALICE/DUM o vacio)", "")
    conn = await _connect()
    try:
        if base:
            b = await load_base(conn, base)
            for k in OCEAN_KEYS:
                cfg[k] = float(b[k])
            cfg["system_prompt"] = b.get("system_prompt") or ""
            print(f"  clonado de {base}: OCEAN " + " ".join(f"{k[-1].upper()}={cfg[k]:.2f}" for k in OCEAN_KEYS))
            adj = _ask("Ajustar los OCEAN? (s/N)", "N")
        else:
            adj = "s"
    finally:
        await conn.close()
    if adj.lower().startswith("s"):
        for k in OCEAN_KEYS:
            cfg[k] = _clamp01(_ask(f"  {OCEAN_LABEL[k]} (0.0-1.0)", str(cfg.get(k, 0.5))))
    if not cfg.get("system_prompt"):
        cfg["system_prompt"] = _ask("System prompt / instrucciones base", f"Eres {cfg['name']}, {cfg['role']}.")
    cfg["memory_access"] = _ask(f"Acceso a memoria ({VALID_SCOPES_HINT})", "own")
    cfg["model"] = _ask("Cerebro (claude/codex/local:<modelo>)", "claude")
    # --- personalizacion mas profunda (mejora 9-jul) ---
    cfg["communication_style"] = _ask("Estilo de comunicacion (formal/casual/directo/calido)", "directo")
    cfg["verbosity"] = _ask("Verbosidad (conciso/balanceado/detallado)", "balanceado")
    cfg["expertise"] = _ask("Areas de experticia (ej: 'codigo, seguridad') (opcional)", "")
    cfg["emotional_baseline"] = _clamp01(_ask("Animo base: valencia 0=serio .. 1=alegre", "0.6"))
    rules = _ask("Reglas base separadas por ';' (opcional)", "")
    cfg["rules"] = [r.strip() for r in rules.split(";") if r.strip()]
    return cfg


async def main():
    ap = argparse.ArgumentParser(description="Crear/personalizar un agente SOUL en el device")
    ap.add_argument("--config", help="ruta a JSON con la config del agente")
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo muestra que haria")
    ap.add_argument("--list", action="store_true", help="lista los agentes existentes en el alma local")
    args = ap.parse_args()

    if args.list:
        conn = await _connect()
        try:
            rows = await conn.fetch(
                "SELECT name, role, ocean_o,ocean_c,ocean_e,ocean_a,ocean_n, active "
                "FROM soul_v3.agents ORDER BY active DESC, name")
            print(f"=== Agentes en el alma local ({len(rows)}) ===")
            for r in rows:
                oc = {k: float(r[k]) for k in OCEAN_KEYS}
                flag = "" if r["active"] else " (inactivo)"
                print(f"  {r['name']:16}{flag}  {_describe_ocean(oc)}")
        finally:
            await conn.close()
        return

    if args.config:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("base"):
            conn = await _connect()
            try:
                b = await load_base(conn, cfg["base"])
            finally:
                await conn.close()
            for k in OCEAN_KEYS:
                cfg.setdefault(k, float(b[k]))
            cfg.setdefault("system_prompt", b.get("system_prompt") or "")
    else:
        cfg = await wizard()

    res = await provision(cfg, dry_run=args.dry_run)
    print("\n" + _summary(cfg, res["ocean"], res["persona"]))
    if res["written"]:
        print("\nArchivos generados:")
        for w in res["written"]:
            print("  " + w)
    print("\n[OK] agente " + ("SIMULADO (dry-run)" if args.dry_run else "creado en el alma local") + ".")


if __name__ == "__main__":
    asyncio.run(main())
