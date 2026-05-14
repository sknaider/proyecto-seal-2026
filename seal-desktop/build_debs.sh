#!/usr/bin/env bash
# Build dual .deb packages for SEAL Companion + SEAL Team Dashboard
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SCRIPT_DIR/dist/deb"
mkdir -p "$OUT"

echo "[1/4] Building companion_core UI (standalone :8769)..."
cd "$SCRIPT_DIR/companion_core/ui" && npm run build

echo "[2/4] Building team dashboard UI (SEAL Studio :8800)..."
cd "$SCRIPT_DIR/ui" && npm run build

echo "[3/4] Packaging seal-companion..."
PKG="$SCRIPT_DIR/debian/seal-companion"
chmod 755 "$PKG/usr/bin/seal-companion"
# Clean previous build artifacts
rm -rf "$PKG/usr/share/seal-companion"
# Copy companion_core Python source
mkdir -p "$PKG/usr/share/seal-companion"
cp -r "$SCRIPT_DIR/companion_core/companion_core" "$PKG/usr/share/seal-companion/"
cp "$SCRIPT_DIR/companion_core/requirements.txt" "$PKG/usr/share/seal-companion/"
# Copy companion_core UI dist
mkdir -p "$PKG/usr/share/seal-companion/ui"
cp -r "$SCRIPT_DIR/companion_core/ui/dist/"* "$PKG/usr/share/seal-companion/ui/"
dpkg-deb --build "$PKG" "$OUT/seal-companion_0.3.0_arm64.deb"
echo "  → $OUT/seal-companion_0.3.0_arm64.deb"

echo "[4/4] Packaging seal-team-dashboard..."
PKG2="$SCRIPT_DIR/debian/seal-team-dashboard"
chmod 755 "$PKG2/usr/bin/seal-team-dashboard"
rm -rf "$PKG2/usr/share/seal-team-dashboard/ui"
mkdir -p "$PKG2/usr/share/seal-team-dashboard/ui"
cp -r "$SCRIPT_DIR/ui/dist/"* "$PKG2/usr/share/seal-team-dashboard/ui/"
dpkg-deb --build "$PKG2" "$OUT/seal-team-dashboard_0.1.0_arm64.deb"
echo "  → $OUT/seal-team-dashboard_0.1.0_arm64.deb"

echo ""
echo "✓ Both packages built in $OUT"
ls -lh "$OUT/"
