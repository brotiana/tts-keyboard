#!/usr/bin/env bash
# Installation locale : aucune connexion sans l'option explicite --en-ligne.
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

ONLINE=0
case "${1:-}" in
  "") ;;
  --en-ligne) ONLINE=1 ;;
  -h|--help)
    echo "Usage : bash installer.sh [--en-ligne]"
    echo "Par défaut : installation hors ligne depuis wheelhouse/."
    exit 0 ;;
  *) echo "Option inconnue. Usage : bash installer.sh [--en-ligne]" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  echo "Trop d'arguments." >&2
  exit 2
fi
if ! command -v python3 >/dev/null; then
  echo "Python 3 manque. Sous Linux Mint : sudo apt install python3 python3-venv python3-tk libportaudio2" >&2
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
  echo "Ce paquet hors ligne est prévu pour Python 3.12 (Linux Mint 22)." >&2
  echo "Utilisez Python 3.12 pour installer et lancer cette application." >&2
  exit 1
fi
shopt -s nullglob
wheels=("$APP_DIR"/wheelhouse/*.whl)
if (( ONLINE == 0 && ${#wheels[@]} == 0 )); then
  echo "Aucun paquet dans wheelhouse/. Aucune connexion n'a été effectuée." >&2
  echo "Au cybercafé : python3 preparer_hors_ligne.py ; rapportez tout le dossier wheelhouse/." >&2
  echo "Puis relancez : bash installer.sh" >&2
  echo "Installation avec Internet uniquement si souhaitée : bash installer.sh --en-ligne" >&2
  exit 1
fi
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  if ! python3 -m venv "$APP_DIR/.venv"; then
    echo "Impossible de créer .venv. Vérifiez le paquet python3-venv." >&2
    echo "À installer avec une connexion, si nécessaire : sudo apt install python3-venv python3-tk libportaudio2" >&2
    exit 1
  fi
fi
if (( ONLINE )); then
  "$APP_DIR/.venv/bin/python" -m pip --isolated --disable-pip-version-check install \
    --only-binary=:all: --find-links "$APP_DIR/wheelhouse" -r "$APP_DIR/requirements.txt"
else
  if ! "$APP_DIR/.venv/bin/python" -m pip --isolated --disable-pip-version-check install \
    --no-index --only-binary=:all: --find-links "$APP_DIR/wheelhouse" -r "$APP_DIR/requirements.txt"; then
    echo "Installation hors ligne incomplète. Aucun téléchargement de secours n'a été tenté." >&2
    echo "Relancez preparer_hors_ligne.py au cybercafé et recopiez tout wheelhouse/." >&2
    exit 1
  fi
fi
echo "Installation terminée. Lancement : bash lancer.sh"
echo "Les modèles doivent être placés séparément dans models/ (voir TELECHARGEMENTS.html)."
