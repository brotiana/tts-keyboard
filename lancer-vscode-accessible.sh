#!/usr/bin/env bash
set -euo pipefail
# Fermez d'abord toutes les fenêtres VS Code pour appliquer ce paramètre Electron.
exec code --force-renderer-accessibility "$@"
