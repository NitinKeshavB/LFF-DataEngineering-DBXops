#!/usr/bin/env bash
# Add the pinned Lakeflow Framework as the read-only submodule framework/lakeflow_framework.
#
#   scripts/add_framework.sh
#
# Reads repo, tag and commit from framework/UPSTREAM.lock, adds the submodule and checks out the pinned
# commit. The cookiecutter's post-generation hook runs it; run it again by hand if that step failed.
# DBXOPS_FRAMEWORK_URL replaces the repository URL (a local mirror, or an offline copy in tests).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBMODULE="framework/lakeflow_framework"
LOCK="$ROOT/framework/UPSTREAM.lock"
URL="${DBXOPS_FRAMEWORK_URL:-$(sed -n 's/^repo=//p' "$LOCK")}"
PINNED="$(sed -n 's/^commit=//p' "$LOCK")"

if [ "$(git -C "$ROOT/$SUBMODULE" rev-parse HEAD 2>/dev/null || true)" = "$PINNED" ]; then
    echo "framework submodule already at $PINNED"
    exit 0
fi

GIT=(git)
if [ -n "${DBXOPS_FRAMEWORK_URL:-}" ]; then
    GIT=(git -c protocol.file.allow=always)
fi
cd "$ROOT"
"${GIT[@]}" submodule add "$URL" "$SUBMODULE"
git -C "$SUBMODULE" checkout --quiet "$PINNED"
git add "$SUBMODULE"
echo "added $SUBMODULE at $PINNED"
