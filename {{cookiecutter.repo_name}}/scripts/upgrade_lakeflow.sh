#!/usr/bin/env bash
# Move the pinned Lakeflow Framework to a release tag and bring everything that mentions the version in step.
#
#   scripts/upgrade_lakeflow.sh <tag>        e.g. scripts/upgrade_lakeflow.sh v0.25.0
#
# Updates the submodule checkout, framework/UPSTREAM.lock, the version in conf/environments.yml,
# framework/databricks.yml and every bundle's databricks.yml, the vendored dataflow-spec skill and the generated
# config copies. It then validates every spec with the new framework's own validator. The submodule itself is never
# edited. Review the result, run `make test`, and commit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBMODULE="$ROOT/framework/lakeflow_framework"
tag="${1:?usage: scripts/upgrade_lakeflow.sh <tag>}"
case "$tag" in v[0-9]*.[0-9]*.[0-9]*) ;; *) echo "error: '$tag' is not a release tag like v0.25.0" >&2; exit 1 ;; esac

old="$(sed -n 's/^tag=//p' "$ROOT/framework/UPSTREAM.lock")"
repo="$(sed -n 's/^repo=//p' "$ROOT/framework/UPSTREAM.lock")"
git -C "$SUBMODULE" fetch --tags --quiet
if ! git -C "$SUBMODULE" rev-parse -q --verify "refs/tags/$tag^{commit}" > /dev/null; then
    echo "error: tag $tag does not exist in $repo" >&2
    exit 1
fi

git -C "$SUBMODULE" checkout -q "$tag"
commit="$(git -C "$SUBMODULE" rev-parse HEAD)"
printf 'repo=%s\ntag=%s\ncommit=%s\n' "$repo" "$tag" "$commit" > "$ROOT/framework/UPSTREAM.lock"

if [ "$old" != "$tag" ]; then
    files=("$ROOT/conf/environments.yml" "$ROOT/framework/databricks.yml")
    while IFS= read -r file; do files+=("$file"); done < <(find "$ROOT/bundles" -maxdepth 2 -name databricks.yml)
    for file in "${files[@]}"; do sed -i "s#$old#$tag#g" "$file"; done
fi

(cd "$ROOT" && python scripts/vendor_skills.py sync lakeflow-dataflowspec-builder)
(cd "$ROOT" && python -m dbxops.metadata --root . substitutions)
(cd "$ROOT" && python -I "$SUBMODULE/scripts/validate_dataflows.py" bundles)
echo "upgraded the framework pin: $old -> $tag ($commit). Review the diff, run 'make test', and commit."
