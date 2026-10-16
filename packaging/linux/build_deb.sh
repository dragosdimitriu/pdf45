#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build a .deb from the PyInstaller output.
#
# Usage (from the repository root, after `pyinstaller packaging/pdf45.spec`):
#   packaging/linux/build_deb.sh [version] [arch]
# Output: dist/pdf45_<version>_<arch>.deb
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${1:-$(python3 -c 'import tomllib,pathlib;print(tomllib.loads(pathlib.Path("'"$ROOT"'/pyproject.toml").read_text())["project"]["version"])')}"
ARCH="${2:-$(dpkg --print-architecture 2>/dev/null || echo amd64)}"
BUNDLE="$ROOT/dist/pdf45"
PKG="pdf45"
STAGE="$ROOT/dist/deb/${PKG}_${VERSION}_${ARCH}"

[ -x "$BUNDLE/pdf45" ] || { echo "dist/pdf45/pdf45 not found - run pyinstaller first" >&2; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt/pdf45" "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/512x512/apps" \
         "$STAGE/usr/share/doc/$PKG"

cp -a "$BUNDLE/." "$STAGE/opt/pdf45/"
ln -s /opt/pdf45/pdf45 "$STAGE/usr/bin/pdf45"
cp "$ROOT/packaging/linux/pdf45.desktop" "$STAGE/usr/share/applications/pdf45.desktop"
cp "$ROOT/src/pdf45/resources/pdf45.png" "$STAGE/usr/share/icons/hicolor/512x512/apps/pdf45.png"
cp "$ROOT/LICENSE" "$STAGE/usr/share/doc/$PKG/copyright"

SIZE_KB=$(du -sk "$STAGE/opt" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: text
Priority: optional
Architecture: $ARCH
Installed-Size: $SIZE_KB
Maintainer: Dragos Dimitriu
Depends: libc6, libglib2.0-0, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xfixes0, libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0, libfontconfig1, libfreetype6, libgl1, libegl1, libdbus-1-3, libxrender1, libxi6, libsm6, libice6, libcups2
Recommends: cups
Homepage: https://github.com/dragosdimitriu/pdf45
Description: pdf/45 - small, free PDF viewer
 pdf/45 is a lightweight PDF viewer built on Qt 6 and PDFium.
 It supports continuous and single-page viewing, zoom, text search,
 outline (bookmark) navigation and printing. Licensed GPL-3.0-or-later.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
exit 0
EOF
cp "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

OUT="$ROOT/dist/${PKG}_${VERSION}_${ARCH}.deb"
if command -v fakeroot >/dev/null 2>&1; then
  fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
else
  dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
fi
echo "built $OUT"
