#!/bin/sh
# Build the macOS app and disk image. From the repo root, on a Mac:
#   ./packaging/build_app.sh [version]   ->  dist/SCuMV26.app and dist/SCuMV26-<ver>-macos.dmg
# Needs Python with host/requirements.txt and pyinstaller installed.
set -e
VERSION="${1:-0.0.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
APP_VERSION="$VERSION" python -m PyInstaller --noconfirm --distpath dist --workpath build/pyinstaller \
    packaging/SCuMV26.spec

# A disk image with the app and a link to /Applications to drag it onto.
STAGE="$(mktemp -d)"
cp -R dist/SCuMV26.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "dist/SCuMV26-$VERSION-macos.dmg"
hdiutil create -volname "SCuMV26" -srcfolder "$STAGE" -ov -format UDZO "dist/SCuMV26-$VERSION-macos.dmg"
rm -rf "$STAGE"
