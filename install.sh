#!/bin/sh
set -eu
if ! command -v uv >/dev/null 2>&1; then
    installer=$(mktemp)
    trap 'rm -f "$installer"' EXIT
    curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$installer"
    sh "$installer"
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi
source_package='https://github.com/AndreLYL/wechat2dot/releases/download/v2.0.0a2/wechat2dot-2.0.0a2-py3-none-any.whl'
if [ -f "$0" ]; then
    checkout=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
    if [ -f "$checkout/pyproject.toml" ] && [ -d "$checkout/wechat2dot/dot" ]; then
        source_package=$checkout
    fi
fi
uv tool install --upgrade "$source_package"
exec "$(uv tool dir --bin)/wechat2dot" setup "$@"
