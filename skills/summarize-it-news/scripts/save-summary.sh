#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
exec /usr/bin/env python3 "$SCRIPT_DIR/save_summary.py" "$@"
