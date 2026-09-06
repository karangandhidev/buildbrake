#!/bin/sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_ROOT=${BUILDBRAKE_INSTALL_ROOT:-"$HOME/.local/share/buildbrake"}
EDITABLE=0

if [ "${1:-}" = "--editable" ]; then
  EDITABLE=1
  shift
fi
if [ "$#" -ne 0 ]; then
  echo "Usage: ./install.sh [--editable]" >&2
  exit 2
fi

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

BUILDBRAKE_SOURCE_DIR="$SOURCE_DIR" BUILDBRAKE_EDITABLE="$EDITABLE" \
  "$INSTALL_ROOT/venv/bin/python" -c '
import os, site
from pathlib import Path
link = Path(site.getsitepackages()[0]) / "buildbrake-development.pth"
if os.environ["BUILDBRAKE_EDITABLE"] == "1":
    source = Path(os.environ["BUILDBRAKE_SOURCE_DIR"]) / "src"
    link.write_text(f"import sys; sys.path.insert(0, {str(source)!r})\n")
elif link.exists():
    link.unlink()
'

if [ -n "${BUILDBRAKE_BIN_DIR:-}" ]; then
  BIN_DIR=$BUILDBRAKE_BIN_DIR
elif [ -d /opt/homebrew/bin ] && [ -w /opt/homebrew/bin ]; then
  BIN_DIR=/opt/homebrew/bin
else
  BIN_DIR="$HOME/.local/bin"
fi

mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_ROOT/venv/bin/buildbrake" "$BIN_DIR/buildbrake"
ln -sf "$INSTALL_ROOT/venv/bin/buildbrake" "$BIN_DIR/bb"

echo
echo "BuildBrake installed successfully."
if [ "$EDITABLE" -eq 1 ]; then
  echo "Development mode: source edits are used directly."
fi
echo "Commands: $BIN_DIR/bb (short) and $BIN_DIR/buildbrake (full)"

case ":$PATH:" in
  *":$BIN_DIR:"*)
    "$BIN_DIR/bb" --help >/dev/null
    echo "Run it from any project with: bb serve"
    ;;
  *)
    echo "Add this directory to PATH, then open a new terminal:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    echo "You can use it immediately with: $BIN_DIR/bb serve"
    ;;
esac
