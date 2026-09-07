#!/usr/bin/env bash
# seal_venv.sh — venvs DESECHABLES para probar SOUL (Core/Platform) sin tocar el sistema.
# Cada venv vive en su propia carpeta bajo ~/seal-venvs y se borra con un rm -rf.
# NO toca el Python ni los paquetes del sistema (PEP 668-safe).
#
# Uso:
#   seal_venv.sh nuevo <nombre> [paquete...]   crea un venv fresco (default: soul-framework)
#   seal_venv.sh lista                          lista los venvs existentes + tamaño
#   seal_venv.sh correr <nombre> <cmd...>       corre un comando DENTRO del venv
#   seal_venv.sh borrar <nombre>                borra un venv
#   seal_venv.sh borrar-todos                   borra todos
#
# Ej.: seal_venv.sh nuevo prueba1               -> venv con Core
#      seal_venv.sh correr prueba1 soul --help  -> usa el CLI aislado
#      seal_venv.sh borrar prueba1              -> lo elimina
set -euo pipefail

ROOT="${SEAL_VENV_DIR:-$HOME/seal-venvs}"
mkdir -p "$ROOT"

cmd="${1:-ayuda}"; shift 2>/dev/null || true

case "$cmd" in
  nuevo)
    name="${1:?nombre requerido: seal_venv.sh nuevo <nombre> [paquetes...]}"; shift || true
    pkgs=("$@"); [ "${#pkgs[@]}" -eq 0 ] && pkgs=("soul-framework")
    dir="$ROOT/$name"
    [ -e "$dir" ] && { echo "✗ ya existe '$name' ($dir). Borralo primero: seal_venv.sh borrar $name"; exit 1; }
    echo "→ creando venv desechable '$name'…"
    python3 -m venv "$dir"
    "$dir/bin/pip" -q install --upgrade pip
    "$dir/bin/pip" install "${pkgs[@]}"
    echo "✓ listo '$name' con: ${pkgs[*]}"
    echo "  activar : source $dir/bin/activate   (salir: deactivate)"
    echo "  usar    : seal_venv.sh correr $name <comando>"
    echo "  borrar  : seal_venv.sh borrar $name   (o rm -rf $dir)"
    ;;
  lista)
    if [ -n "$(ls -A "$ROOT" 2>/dev/null)" ]; then
      du -sh "$ROOT"/*/ 2>/dev/null | sed 's#'"$ROOT"'/##; s#/$##'
    else echo "(no hay venvs)"; fi
    ;;
  correr)
    name="${1:?nombre requerido}"; shift || true
    dir="$ROOT/$name"
    [ -d "$dir" ] || { echo "✗ no existe '$name'"; exit 1; }
    # shellcheck disable=SC1091
    source "$dir/bin/activate"
    "$@"
    ;;
  borrar)
    name="${1:?nombre requerido}"
    rm -rf "${ROOT:?}/${name:?}" && echo "✓ borrado '$name'"
    ;;
  borrar-todos)
    rm -rf "${ROOT:?}"/* 2>/dev/null || true; echo "✓ todos los venvs borrados"
    ;;
  *)
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
