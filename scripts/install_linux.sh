#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

command -v python3 >/dev/null 2>&1 || { echo 'Instala Python 3.10+ y vuelve a ejecutar este script.' >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo 'Instala Node.js 20.19+ LTS y vuelve a ejecutar este script.' >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo 'npm no está disponible; reinstala Node.js LTS con npm.' >&2; exit 1; }
node_version="$(node --version | sed -E 's/^v//; s/-.*//')"
if ! python3 - "$node_version" <<'PY'
import sys
parts = tuple(int(part) for part in sys.argv[1].split('.')[:2])
raise SystemExit(0 if parts >= (20, 19) else 1)
PY
then
  echo "Node.js $node_version es antiguo; RigM requiere 20.19+ LTS." >&2
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  if [[ "$EUID" -eq 0 ]]; then
    apt_cmd=(apt-get)
  elif command -v sudo >/dev/null 2>&1; then
    apt_cmd=(sudo apt-get)
  else
    echo 'Faltan bibliotecas Qt; instala las dependencias apt con privilegios de administrador.' >&2
    exit 1
  fi
  "${apt_cmd[@]}" update
  "${apt_cmd[@]}" install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 \
    libxkbcommon-x11-0 libegl1 libgl1 libnss3 libfontconfig1 libx11-xcb1
fi

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install --no-build-isolation -e '.[rigging,avatar3d]'

pushd viewer3d >/dev/null
npm ci
npm run build
popd >/dev/null

cat <<'EOF'

Instalación lista. Ejecuta:
  .venv/bin/rigm
  .venv/bin/rigmctl --help
El auto-rig y los clips 3D del visor funcionan sin Blender.
En escritorios Linux mínimos puede ser necesario instalar las bibliotecas Qt/WebEngine del sistema.
EOF
