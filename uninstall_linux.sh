#!/usr/bin/env bash
set -euo pipefail

EXTENSION_ID="org.baimard.libreoffice.ai.translator"

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

"$UNOPKG" remove "$EXTENSION_ID" || true

if [[ "$UNOPKG" == /snap/* ]]; then
  SNAP_PROFILE="$HOME/snap/libreoffice/current/.config/libreoffice/4/user"
  rm -rf "$SNAP_PROFILE/uno_packages/cache" 2>/dev/null || true
  rm -rf "$SNAP_PROFILE/extensions/tmp" 2>/dev/null || true
fi

echo "Extension supprimée. Relancez LibreOffice."
