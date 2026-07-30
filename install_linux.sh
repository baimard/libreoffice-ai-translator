#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

EXTENSION_ID="org.baimard.libreoffice.ai.translator"
OXT="$ROOT_DIR/dist/libreoffice-ai-translator.oxt"

for cmd in python3 zip; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "ERREUR : $cmd introuvable."
    exit 1
  }
done

pkill -f soffice.bin 2>/dev/null || true
pkill -f libreoffice 2>/dev/null || true
sleep 2

UNOPKG="${UNOPKG:-}"
if [[ -z "$UNOPKG" ]]; then
  for candidate in \
    /snap/libreoffice/current/lib/libreoffice/program/unopkg \
    "$(command -v unopkg 2>/dev/null || true)" \
    /usr/lib/libreoffice/program/unopkg \
    /opt/libreoffice*/program/unopkg; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      UNOPKG="$candidate"
      break
    fi
  done
fi

if [[ -z "$UNOPKG" ]]; then
  echo "ERREUR : unopkg introuvable."
  exit 1
fi

echo "Utilisation de : $UNOPKG"
make clean
make build

"$UNOPKG" remove "$EXTENSION_ID" >/dev/null 2>&1 || true

# Nettoyage ciblé du cache utilisateur Snap lorsque le binaire Snap est utilisé.
if [[ "$UNOPKG" == /snap/* ]]; then
  SNAP_PROFILE="$HOME/snap/libreoffice/current/.config/libreoffice/4/user"
  rm -rf "$SNAP_PROFILE/uno_packages/cache" 2>/dev/null || true
  rm -rf "$SNAP_PROFILE/extensions/tmp" 2>/dev/null || true
fi

printf 'yes\n' | "$UNOPKG" add --force "$OXT"

echo
echo "Extension installée :"
"$UNOPKG" list | grep -i -A 8 -B 1 "$EXTENSION_ID" || true

echo
echo "Fermez complètement LibreOffice puis relancez-le."
echo "Journal de diagnostic :"
echo "  find \"$HOME\" /tmp -type f -path '*/ai-translator/extension.log' 2>/dev/null"
