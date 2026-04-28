#!/usr/bin/env bash
# Sprint 1 / Mitigaciu00f3n A u2014 Git checkpoint pre-edit.
#
# Hook PreToolUse para Edit/MultiEdit/Write.
# Si el archivo target matchea un patru00f3n de critical_paths.yaml, hace
# SHA256 snapshot + git stash con mensaje trazable.
# Anti stash-storm: si ya hay stash <2s del mismo archivo, skipea.
#
# stdin  u2192 JSON con {"tool_name", "tool_input": {"file_path"}, "agent", "trace_id"}
# stdout u2192 JSON {"decision":"allow", "checkpoint": "<stash_ref>|skipped|clean_baseline"}
# exit   u2192 0 siempre (nunca bloquea)

set -uo pipefail

REPO_ROOT="/home/dadito/IA/proyecto-seal"
CRITICAL_YAML="${REPO_ROOT}/memory/critical_paths.yaml"
STAMP_DIR="/tmp/seal_checkpoint_stamps"
LOG_FILE="${REPO_ROOT}/memory/logs/pre_edit_checkpoint.jsonl"
mkdir -p "$STAMP_DIR" "$(dirname "$LOG_FILE")"

PAYLOAD=$(cat)

# Helper: print JSON allow result and exit 0
emit() {
  printf '%s\n' "$1"
  printf '%s\n' "$1" >> "$LOG_FILE"
  exit 0
}

if [[ -z "${PAYLOAD// }" ]]; then
  emit '{"decision":"allow","checkpoint":"empty_payload"}'
fi

# Extraer campos vu00eda python
parsed=$(SEAL_PAYLOAD="$PAYLOAD" python3 -c '
import json, os, sys
raw = os.environ.get("SEAL_PAYLOAD","")
try:
    d = json.loads(raw or "{}")
except Exception:
    print("__ERR__"); sys.exit(0)
tool = d.get("tool_name") or d.get("tool") or ""
ti = d.get("tool_input") or d.get("input") or {}
fp = ti.get("file_path") or ti.get("path") or ""
agent = d.get("agent") or os.environ.get("SEAL_AGENT","unknown")
trace = d.get("trace_id","-")
print(f"{tool}\t{fp}\t{agent}\t{trace}")
' 2>/dev/null)

if [[ "$parsed" == "__ERR__" || -z "$parsed" ]]; then
  emit '{"decision":"allow","checkpoint":"unparseable_payload"}'
fi

IFS=$'\t' read -r TOOL FILE_PATH AGENT TRACE_ID <<<"$parsed"

case "$TOOL" in
  Edit|MultiEdit|Write) ;;
  *) emit "{\"decision\":\"allow\",\"checkpoint\":\"tool_not_guarded\"}" ;;
esac

if [[ -z "$FILE_PATH" ]]; then
  emit '{"decision":"allow","checkpoint":"no_file_path"}'
fi

# u00bfEs path cru00edtico?
is_critical=$(python3 - "$FILE_PATH" "$CRITICAL_YAML" <<'PY'
import sys, fnmatch, os
fp = os.path.abspath(sys.argv[1])
yaml_path = sys.argv[2]
if not os.path.exists(yaml_path):
    print("no"); sys.exit(0)
paths, excludes = [], []
section = None
for line in open(yaml_path, encoding="utf-8"):
    s = line.strip()
    if s.startswith("paths:"): section="p"; continue
    if s.startswith("exclude:"): section="x"; continue
    if section and s.startswith("- "):
        v = s[2:].strip().strip('"').strip("'")
        if section=="p": paths.append(v)
        else: excludes.append(v)
    elif section and not s.startswith("#") and s and not s.startswith("-"):
        section = None
for pat in excludes:
    if fnmatch.fnmatch(fp, pat): print("no"); sys.exit(0)
for pat in paths:
    if fp == pat or fnmatch.fnmatch(fp, pat):
        print("yes"); sys.exit(0)
print("no")
PY
)

if [[ "$is_critical" != "yes" ]]; then
  emit "{\"decision\":\"allow\",\"checkpoint\":\"path_not_critical\",\"file\":\"$FILE_PATH\"}"
fi

# Anti stash-storm: si hay stamp <2s del mismo archivo, skip
STAMP_KEY=$(echo -n "$FILE_PATH" | md5sum | cut -d' ' -f1)
STAMP_FILE="$STAMP_DIR/$STAMP_KEY"
NOW=$(date +%s)
if [[ -f "$STAMP_FILE" ]]; then
  LAST=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
  DELTA=$(( NOW - LAST ))
  if (( DELTA < 2 )); then
    emit "{\"decision\":\"allow\",\"checkpoint\":\"skipped_storm\",\"delta_s\":$DELTA}"
  fi
fi
echo "$NOW" > "$STAMP_FILE"

# Detectar git root del archivo
FILE_DIR=$(dirname "$FILE_PATH")
GIT_ROOT=$(git -C "$FILE_DIR" rev-parse --show-toplevel 2>/dev/null || echo "")

if [[ -z "$GIT_ROOT" ]]; then
  emit "{\"decision\":\"allow\",\"checkpoint\":\"not_a_git_repo\",\"file\":\"$FILE_PATH\"}"
fi

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STASH_MSG="PRE-EDIT $AGENT $FILE_PATH $TS trace=$TRACE_ID"

# Mitigation F u2014 SHA256 snapshot pre-edit (siempre, incluso si no hay cambios)
mkdir -p /tmp/seal_hashes
HASH_KEY=$(python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])" "$FILE_PATH" 2>/dev/null)
if [[ -n "$HASH_KEY" && -f "$FILE_PATH" ]]; then
  sha256sum "$FILE_PATH" | awk '{print $1}' | cut -c1-16 > "/tmp/seal_hashes/${HASH_KEY}.pre" 2>/dev/null || true
fi

# Solo stashear si hay cambios sin commit en ese archivo
if git -C "$GIT_ROOT" diff --quiet -- "$FILE_PATH" 2>/dev/null && \
   git -C "$GIT_ROOT" diff --cached --quiet -- "$FILE_PATH" 2>/dev/null && \
   [[ -z "$(git -C "$GIT_ROOT" ls-files --others --exclude-standard -- "$FILE_PATH" 2>/dev/null)" ]]; then
  # Archivo limpio u2014 registrar checkpoint vu00eda SHA HEAD
  HEAD_SHA=$(git -C "$GIT_ROOT" rev-parse HEAD 2>/dev/null || echo "")
  RESULT="{\"decision\":\"allow\",\"checkpoint\":\"clean_baseline\",\"head\":\"$HEAD_SHA\",\"file\":\"$FILE_PATH\",\"agent\":\"$AGENT\",\"trace_id\":\"$TRACE_ID\"}"
else
  # Path relativo para evitar error de git stash con ruta absoluta
  REL_PATH=$(realpath --relative-to="$GIT_ROOT" "$FILE_PATH" 2>/dev/null || echo "$FILE_PATH")
  STASH_OUT=$(git -C "$GIT_ROOT" stash push --keep-index --include-untracked -m "$STASH_MSG" -- "$REL_PATH" 2>&1)
  STASH_RC=$?
  if (( STASH_RC == 0 )); then
    STASH_REF=$(git -C "$GIT_ROOT" stash list -1 --pretty='%gd' 2>/dev/null || echo "stash@{0}")
    RESULT="{\"decision\":\"allow\",\"checkpoint\":\"stashed\",\"stash_ref\":\"$STASH_REF\",\"file\":\"$FILE_PATH\",\"agent\":\"$AGENT\",\"trace_id\":\"$TRACE_ID\"}"
  else
    RESULT="{\"decision\":\"allow\",\"checkpoint\":\"stash_failed\",\"file\":\"$FILE_PATH\",\"err\":$(printf '%s' "$STASH_OUT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()[:300]))')}"
  fi
fi

emit "$RESULT"
