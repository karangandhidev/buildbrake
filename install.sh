#!/bin/sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_ROOT=${BUILDBRAKE_INSTALL_ROOT:-"$HOME/.local/share/buildbrake"}

if ! command -v python3 >/dev/null 2>&1; then
  echo "BuildBrake requires Python 3.9 or newer." >&2
  exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  echo "BuildBrake requires Python 3.9 or newer; found Python $PYTHON_VERSION." >&2
  exit 1
fi

python3 -m venv "$INSTALL_ROOT/venv"
"$INSTALL_ROOT/venv/bin/python" -m pip install --disable-pip-version-check --upgrade "$SOURCE_DIR"

if [ -n "${BUILDBRAKE_BIN_DIR:-}" ]; then
  BIN_DIR=$BUILDBRAKE_BIN_DIR
elif [ -d /opt/homebrew/bin ] && [ -w /opt/homebrew/bin ]; then
  BIN_DIR=/opt/homebrew/bin
else
  BIN_DIR="$HOME/.local/bin"
fi

mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_ROOT/venv/bin/buildbrake" "$BIN_DIR/buildbrake"

echo
echo "BuildBrake installed successfully."
echo "Command: $BIN_DIR/buildbrake"

case ":$PATH:" in
  *":$BIN_DIR:"*)
    "$BIN_DIR/buildbrake" --help >/dev/null
    echo "Run it from any project with: buildbrake serve"
    ;;
  *)
    echo "Add this directory to PATH, then open a new terminal:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    echo "You can use it immediately with: $BIN_DIR/buildbrake serve"
    ;;
esac
