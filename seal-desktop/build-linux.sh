#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[linux] building Debian packages"
"$ROOT/build_debs.sh"

echo "[linux] building Tauri desktop bundles"
npm --prefix "$ROOT" run tauri -- build --config src-tauri/tauri.user.conf.json

echo "[linux] artifacts"
find "$ROOT/dist/deb" "$ROOT/src-tauri/target/release/bundle" -maxdepth 3 -type f \
  \( -name '*.deb' -o -name '*.rpm' -o -name '*.AppImage' -o -name 'seal-app' \) \
  -print -exec ls -lh {} \;
