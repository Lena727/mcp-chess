#!/usr/bin/env sh
# One-click launcher for macOS / Linux:  ./start.sh
cd "$(dirname "$0")" || exit 1
if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found - installing it from https://astral.sh/uv ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
fi
exec uv run mcp-chess "$@"
