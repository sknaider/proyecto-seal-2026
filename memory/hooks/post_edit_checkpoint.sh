#!/usr/bin/env bash
# Sprint 1 / Mitigación A — Git checkpoint post-edit.
#
# Hook PostToolUse para Edit/MultiEdit/Write.
# Si el archivo está en critical_paths.yaml, hace commit en branch
# `auto-checkpoint/<agente>` con mensaje trazable. La rama auto-checkpoint
# vive en paralelo a la rama actual (sin afectar el HEAD del trabajo).
#
# stdin  → JSON con {"tool_name", "tool_input": {"file_path"}, "agent", "trace_id"}
# stdout → JSON {"checkpoint": "committed|skipped|...", "commit": "<sha>"}
# exit   → 0 siempre

set -uo pipefail

REPO_ROOT="/home/dadito/IA/proyecto-seal"
CRITICAL_YAML="${REPO_ROOT}/memory/critical_paths.yaml"
LOG_FILE="${REPO_ROOT}/memory/logs/post_edit_checkpoint.jsonl"
mkdir -p "$(dirname "$LOG_FILE")"

PAYLOAD=$(cat)

emit() { printf '%s\n' "$1"; printf '%s\n' "$1" >> "$LOG_FILE"; exit 0; }

[[ -z "${PAYLOAD// }" ]] && emit '{"checkpoint":"empty_payload"}'

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

[[ "$parsed" == "__ERR__" || -z "$parsed" ]] && emit '{"checkpoint":"unparseable"}'
IFS=$'\t' read -r TOOL FILE_PATH AGENT TRACE_ID <<<"$parsed"

case "$TOOL" in
  Edit|MultiEdit|Write) ;;
  *) emit '{"checkpoint":"tool_not_guarded"}' ;;
esac
[[ -z "$FILE_PATH" ]] && emit '{"checkpoint":"no_file"}'

is_critical=$(python3 - "$FILE_PATH" "$CRITICAL_YAML" <<'PY'
import sys, fnmatch, os
fp = os.path.abspath(sys.argv[1]); yaml_path = sys.argv[2]
if not os.path.exists(yaml_path): print("no"); sys.exit(0)
paths, excludes, section = [], [], None
for line in open(yaml_path, encoding="utf-8"):
    s = line.strip()
    if s.startswith("paths:"): section="p"; continue
    if s.startswith("exclude:"): section="x"; continue
    if section and s.startswith("- "):
        v = s[2:].strip().strip('"').strip("'")
        (paths if section=="p" else excludes).append(v)
    elif section and not s.startswith("#") and s and not s.startswith("-"):
        section = None
for pat in excludes:
    if fnmatch.fnmatch(fp, pat): print("no"); sys.exit(0)
for pat in paths:
    if fp == pat or fnmatch.fnmatch(fp, pat): print("yes"); sys.exit(0)
print("no")
PY
)

[[ "$is_critical" != "yes" ]] && emit "{\"checkpoint\":\"path_not_critical\",\"file\":\"$FILE_PATH\"}"

FILE_DIR=$(dirname "$FILE_PATH")
GIT_ROOT=$(git -C "$FILE_DIR" rev-parse --show-toplevel 2>/dev/null || echo "")
[[ -z "$GIT_ROOT" ]] && emit "{\"checkpoint\":\"not_a_git_repo\",\"file\":\"$FILE_PATH\"}"

# Si no hay cambios reales en el archivo, no hay nada que commitear
if git -C "$GIT_ROOT" diff --quiet HEAD -- "$FILE_PATH" 2>/dev/null && \
   [[ -z "$(git -C "$GIT_ROOT" ls-files --others --exclude-standard -- "$FILE_PATH" 2>/dev/null)" ]]; then
  emit "{\"checkpoint\":\"noop_no_changes\",\"file\":\"$FILE_PATH\"}"
fi

CURRENT_BRANCH=$(git -C "$GIT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
CHECKPOINT_BRANCH="auto-checkpoint/${AGENT}"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
COMMIT_MSG="[$AGENT] auto-checkpoint $FILE_PATH $TS trace=$TRACE_ID"

# Estrategia: commit en HEAD actual, después fast-forward la branch auto-checkpoint
# para apuntar al mismo commit. NO cambiamos de branch (no perturbar el trabajo activo).
#
# 1. git add -- <file>
# 2. si hay cambios staged → commit (en branch actual)
# 3. crear/forzar branch auto-checkpoint/<agente> apuntando al commit nuevo

git -C "$GIT_ROOT" add -- "$FILE_PATH" 2>/dev/null
if git -C "$GIT_ROOT" diff --cached --quiet -- "$FILE_PATH" 2>/dev/null; then
  emit "{\"checkpoint\":\"noop_nothing_staged\",\"file\":\"$FILE_PATH\"}"
fi

COMMIT_OUT=$(git -C "$GIT_ROOT" \
  -c user.name="seal-checkpoint" \
  -c user.email="checkpoint@seal.local" \
  commit -m "$COMMIT_MSG" -- "$FILE_PATH" 2>&1)
COMMIT_RC=$?

if (( COMMIT_RC != 0 )); then
  ERR_JSON=$(printf '%s' "$COMMIT_OUT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()[:300]))')
  emit "{\"checkpoint\":\"commit_failed\",\"file\":\"$FILE_PATH\",\"err\":$ERR_JSON}"
fi

NEW_SHA=$(git -C "$GIT_ROOT" rev-parse HEAD 2>/dev/null || echo "")

# Fast-forward (o crear) branch auto-checkpoint/<agente> apuntando al commit nuevo
git -C "$GIT_ROOT" branch -f "$CHECKPOINT_BRANCH" "$NEW_SHA" 2>/dev/null

# Mitigation F — store post-edit hash and log delta
HASH_KEY=$(python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])" "$FILE_PATH" 2>/dev/null)
POST_HASH=""
PRE_HASH=""
if [[ -n "$HASH_KEY" && -f "$FILE_PATH" ]]; then
  POST_HASH=$(sha256sum "$FILE_PATH" | awk '{print $1}' | cut -c1-16)
  PRE_HASH=$(cat "/tmp/seal_hashes/${HASH_KEY}.pre" 2>/dev/null || echo "")
  echo "$POST_HASH" > "/tmp/seal_hashes/${HASH_KEY}.post" 2>/dev/null || true
fi

emit "{\"checkpoint\":\"committed\",\"commit\":\"$NEW_SHA\",\"branch\":\"$CHECKPOINT_BRANCH\",\"current_branch\":\"$CURRENT_BRANCH\",\"file\":\"$FILE_PATH\",\"agent\":\"$AGENT\",\"trace_id\":\"$TRACE_ID\",\"hash_pre\":\"$PRE_HASH\",\"hash_post\":\"$POST_HASH\",\"hash_changed\":$([ \"$PRE_HASH\" != \"$POST_HASH\" ] && echo true || echo false)}"
