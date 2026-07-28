#!/usr/bin/env bash
# Shared SEAL identity/token bootstrap for agent launchers.
# Runtime/config are canonical. Legacy /tmp compatibility is opt-in only.

if [ -z "${SEAL_AGENT:-}" ]; then
  echo "[seal_identity_env] SEAL_AGENT is required" >&2
  return 2 2>/dev/null || exit 2
fi

_seal_agent="$(printf '%s' "$SEAL_AGENT" | tr '[:lower:]' '[:upper:]')"
_seal_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/seal"
_seal_runtime_base="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
_seal_runtime_dir="${SEAL_TOKENS_DIR:-$_seal_runtime_base/seal}"
_seal_legacy_dir="${SEAL_LEGACY_TOKENS_DIR:-/tmp/seal_tokens}"
_seal_token_file="$_seal_runtime_dir/${_seal_agent}.token"
_seal_legacy_token_file="$_seal_legacy_dir/${_seal_agent}.token"
_seal_mode_file="$_seal_config_dir/identity_mode"
_seal_legacy_mode_file="$_seal_legacy_dir/identity_mode"
_seal_legacy_enabled=0
if [ "${SEAL_DISABLE_LEGACY_TMP_TOKENS:-1}" = "0" ]; then
  _seal_legacy_enabled=1
fi

install -d -m 700 "$_seal_config_dir"
install -d -m 700 "$_seal_runtime_dir"
if [ "$_seal_legacy_enabled" = "1" ]; then
  install -d -m 700 "$_seal_legacy_dir"
fi

if [ "$_seal_legacy_enabled" = "1" ] && [ ! -s "$_seal_mode_file" ] && [ -s "$_seal_legacy_mode_file" ]; then
  install -m 600 "$_seal_legacy_mode_file" "$_seal_mode_file"
elif [ ! -s "$_seal_mode_file" ]; then
  # Default-on-missing = MIGRATE durante el dev activo de Fase 1 (consenso equipo + addendum §3.2):
  # el fail-closed ENFORCE-on-missing es el PASO DELIBERADO de sellado (addendum paso 6/9), DESPUÉS
  # de la ventana de observación de la doble-ruta — NO en cada boot (evita brick prematuro de un
  # bridge que aún lee /tmp tras un reboot). Catch doble JARVIS+ALICE 2026-06-10.
  printf '%s\n' "${SEAL_IDENTITY_MODE:-MIGRATE}" > "$_seal_mode_file"
  chmod 600 "$_seal_mode_file"
fi

if [ "$_seal_legacy_enabled" = "1" ] && [ ! -s "$_seal_token_file" ] && [ -s "$_seal_legacy_token_file" ]; then
  install -m 600 "$_seal_legacy_token_file" "$_seal_token_file"
elif [ ! -s "$_seal_token_file" ]; then
  python3 - <<'PY' > "$_seal_token_file"
import secrets
print(secrets.token_hex(32))
PY
  chmod 600 "$_seal_token_file"
fi

if [ "$_seal_legacy_enabled" = "1" ] && [ -s "$_seal_token_file" ]; then
  install -m 600 "$_seal_token_file" "$_seal_legacy_token_file"
fi

export SEAL_AGENT="$_seal_agent"
export SEAL_TOKENS_DIR="$_seal_runtime_dir"
export SEAL_IDENTITY_MODE_FILE="${SEAL_IDENTITY_MODE_FILE:-$_seal_mode_file}"
export SEAL_SESSION_TOKEN="$(cat "$_seal_token_file" 2>/dev/null || true)"

unset _seal_agent _seal_config_dir _seal_runtime_base _seal_runtime_dir
unset _seal_legacy_dir _seal_token_file _seal_legacy_token_file
unset _seal_mode_file _seal_legacy_mode_file _seal_legacy_enabled
