#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

EXTENSION_ID="org.baimard.libreoffice.ai.translator"
OXT="$ROOT_DIR/dist/libreoffice-ai-translator.oxt"

error() {
  echo "ERREUR : $*" >&2
  exit 1
}

for cmd in python3 zip make; do
  command -v "$cmd" >/dev/null 2>&1 || error "$cmd introuvable."
done

UNOPKG="${UNOPKG:-}"
if [[ -z "$UNOPKG" ]]; then
  candidates=(
    "/snap/libreoffice/current/lib/libreoffice/program/unopkg"
    "$(command -v unopkg 2>/dev/null || true)"
    "/usr/lib/libreoffice/program/unopkg"
  )

  for candidate in /opt/libreoffice*/program/unopkg; do
    candidates+=("$candidate")
  done

  for candidate in "${candidates[@]}"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      UNOPKG="$candidate"
      break
    fi
  done
fi

[[ -n "$UNOPKG" && -x "$UNOPKG" ]] || error "unopkg introuvable."

echo "Utilisation de : $UNOPKG"
echo "Fermeture de LibreOffice…"
pkill -f soffice.bin 2>/dev/null || true
pkill -f '/libreoffice' 2>/dev/null || true
sleep 2

echo "Construction de l'extension…"
make clean
make build
[[ -f "$OXT" ]] || error "le fichier OXT n'a pas été généré : $OXT"

echo "Suppression de l'ancienne version…"
"$UNOPKG" remove "$EXTENSION_ID" >/dev/null 2>&1 || true

# Nettoyage ciblé du cache utilisateur Snap lorsque le binaire Snap est utilisé.
if [[ "$UNOPKG" == /snap/* ]]; then
  SNAP_PROFILE="$HOME/snap/libreoffice/current/.config/libreoffice/4/user"
  rm -rf "$SNAP_PROFILE/uno_packages/cache" 2>/dev/null || true
  rm -rf "$SNAP_PROFILE/extensions/tmp" 2>/dev/null || true
fi

echo "Installation de l'extension…"
UNOPKG_ARGS=(add --force)
if "$UNOPKG" --help 2>&1 | grep -q -- '--suppress-license'; then
  UNOPKG_ARGS+=(--suppress-license)
fi

# Ne jamais alimenter unopkg avec `yes` : si la commande échoue ou ferme son
# entrée standard, `yes` continue d'écrire indéfiniment dans le terminal.
"$UNOPKG" "${UNOPKG_ARGS[@]}" "$OXT"

echo "Vérification de l'installation…"
INSTALLED_OUTPUT="$($UNOPKG list 2>&1)"
if ! grep -Fq "$EXTENSION_ID" <<<"$INSTALLED_OUTPUT"; then
  echo "$INSTALLED_OUTPUT" >&2
  error "l'extension n'apparaît pas dans la liste des extensions installées."
fi

echo
echo "Extension installée avec succès :"
grep -i -A 8 -B 1 "$EXTENSION_ID" <<<"$INSTALLED_OUTPUT" || true

echo
echo "Relancez complètement LibreOffice."
echo "Journal de diagnostic :"
echo "  find \"$HOME\" /tmp -type f -path '*/ai-translator/extension.log' 2>/dev/null"
