#!/usr/bin/env bash
# Stage the pinned Lakeflow Framework for deployment.
#
#   scripts/stage_framework.sh [out_dir]      (default: framework/build)
#
# Copies framework/lakeflow_framework/src to <out>/src, overlays framework/local/ onto <out>/src/local/,
# and puts the wrapper bundle definition at <out>/databricks.yml. Refuses to run if the submodule is not
# checked out at the commit pinned in framework/UPSTREAM.lock. Never modifies the submodule.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/framework/build}"
SUBMODULE="$ROOT/framework/lakeflow_framework"
PINNED="$(sed -n 's/^commit=//p' "$ROOT/framework/UPSTREAM.lock")"
ACTUAL="$(git -C "$SUBMODULE" rev-parse HEAD 2>/dev/null || true)"

if [ -z "$ACTUAL" ]; then
    echo "error: framework submodule is not checked out; run: git submodule update --init" >&2
    exit 1
fi
if [ "$ACTUAL" != "$PINNED" ]; then
    echo "error: submodule is at $ACTUAL but framework/UPSTREAM.lock pins $PINNED" >&2
    echo "       use 'make upgrade-lakeflow TAG=<tag>' to move the pin, or 'git submodule update' to restore it" >&2
    exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"
cp -R "$SUBMODULE/src" "$OUT/src"
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +

mkdir -p "$OUT/src/local"
tar -C "$ROOT/framework/local" --exclude=.gitkeep --exclude=OVERLAY.md -cf - . | tar -C "$OUT/src/local" -xf -

cp "$ROOT/framework/databricks.yml" "$OUT/databricks.yml"
echo "$PINNED" > "$OUT/STAGED_FROM_COMMIT"
echo "staged framework $PINNED -> $OUT"
