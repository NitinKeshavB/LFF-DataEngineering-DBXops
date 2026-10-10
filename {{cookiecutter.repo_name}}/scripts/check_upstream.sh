#!/usr/bin/env bash
# Report whether the Lakeflow Framework has a newer release than the pin in framework/UPSTREAM.lock.
#
#   scripts/check_upstream.sh        prints: current=<tag> latest=<tag> update=yes|no
#
# Network access to github.com is required. Only release tags (vMAJOR.MINOR.PATCH) are considered.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
current="$(sed -n 's/^tag=//p' "$ROOT/framework/UPSTREAM.lock")"
repo="$(sed -n 's/^repo=//p' "$ROOT/framework/UPSTREAM.lock")"
latest="$(git ls-remote --tags --refs "$repo" 'v*' | sed 's#.*refs/tags/##' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
update=no
if [ -n "$latest" ] && [ "$latest" != "$current" ] && [ "$(printf '%s\n%s\n' "$current" "$latest" | sort -V | tail -1)" = "$latest" ]; then
    update=yes
fi
echo "current=$current latest=$latest update=$update"
