#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERREUR : python3 introuvable."
  exit 1
fi
if ! command -v zip >/dev/null 2>&1; then
  echo "ERREUR : zip introuvable. Installez le paquet zip."
  exit 1
fi

UNOPKG="${UNOPKG:-}"
if [[ -z "$UNOPKG" ]]; then
  for candidate in \
    "$(command -v unopkg 2>/dev/null || true)" \
    /usr/bin/unopkg \
    /usr/lib/libreoffice/program/unopkg \
    /opt/libreoffice*/program/unopkg \
    /snap/libreoffice/current/lib/libreoffice/program/unopkg; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      UNOPKG="$candidate"
      break
    fi
  done
fi

if [[ -z "$UNOPKG" ]]; then
  echo "ERREUR : unopkg introuvable."
  echo "Installez LibreOffice depuis les paquets de votre distribution ou définissez UNOPKG=/chemin/vers/unopkg."
  exit 1
fi

make build
"$UNOPKG" add --force "$ROOT_DIR/dist/libreoffice-ai-translator.oxt"
echo "Extension installée. Fermez complètement puis relancez LibreOffice."
