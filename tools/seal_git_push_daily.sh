#!/usr/bin/env bash
# seal_git_push_daily.sh — empuja el taller a GitHub cada dia (JARVIS, 7-sep-2026, carril 2 del rediseño).
# DESACTIVADO hasta que exista credencial de GitHub (decision de William). Nunca hace reset ni force.
set -euo pipefail
cd /home/dadito/IA/proyecto-seal
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "[push] no es un repo git todavia"; exit 0; }
git remote get-url github >/dev/null 2>&1 || { echo "[push] sin remoto origin"; exit 0; }
rama=$(git rev-parse --abbrev-ref HEAD)
# GUARDA-DESTRUCTIVA (publicacion): no se empuja si algun archivo TRACKEADO contiene un DSN con clave real.
# Plantillas ({ROLE}, <clave>, REDACTADO, ejemplo) no cuentan. Hallazgo de FABLE 7-sep 11:31.
fugas=$(git grep -I -l -E 'postgres(ql)?://[A-Za-z0-9_]+:[^@{}<> ]{8,}@' -- . ':!*.md' 2>/dev/null | xargs -r grep -L -E 'REDACTADO|\{ROLE\}|\{clave\}|<clave>|ejemplo|example' 2>/dev/null | wc -l)
if [ "$fugas" -gt 0 ]; then echo "[push] BLOQUEADO: $fugas archivo(s) trackeados con DSN y clave; limpiar antes de publicar" >&2; exit 3; fi
git push github "$rama" 2>&1 | tail -2
remoto=$(git ls-remote github "refs/heads/$rama" | cut -c1-40); local=$(git rev-parse HEAD)
[ "$remoto" = "$local" ] && echo "[push] ok $rama $local" || { echo "[push] DIVERGE remoto=$remoto local=$local" >&2; exit 1; }
