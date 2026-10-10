#!/usr/bin/env bash
# Provision one registered project: create its private repository from the template, write its metadata/_platform
# snapshots, give its GitHub team access, create the five GitHub environments with their reviewers, and apply the
# default-branch ruleset. Idempotent where GitHub allows it; safe to re-run for the same project.
#
#   scripts/provision_project.sh <project_id>
#
# Needs: gh authenticated as the platform GitHub App; ORG, TEMPLATE_REPO, PLATFORM_RELEASE_TEAM in the environment;
# dbxops installed. Nothing here has run against a real organisation yet.
set -euo pipefail

project_id="${1:?usage: provision_project.sh <project_id>}"
: "${ORG:?}" "${TEMPLATE_REPO:?}" "${PLATFORM_RELEASE_TEAM:?}"
registry="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
field() { python -c "import sys, yaml; d = yaml.safe_load(open(sys.argv[1])); print(eval(sys.argv[2]))" "$registry/projects/$project_id.yaml" "$1"; }

repo_name="$(field 'd["repo"]["name"]')"
squad_team="$(field 'd["squad"]["github_team"]')"
full="$ORG/$repo_name"

if gh repo view "$full" > /dev/null 2>&1; then
    echo "$full already exists; leaving its history alone and re-applying settings"
else
    gh repo create "$full" --private --template "$ORG/$TEMPLATE_REPO"
    work="$(mktemp -d)"
    # a repository made from a template can take a few seconds to become cloneable
    for attempt in 1 2 3 4 5; do
        if gh repo clone "$full" "$work/repo"; then break; fi
        sleep $((attempt * 3))
    done
    [ -d "$work/repo/.git" ] || { echo "error: could not clone $full" >&2; exit 1; }
    python -m dbxops.registry render "$registry" "$project_id" "$work/repo/metadata/_platform"
    git -C "$work/repo" config user.name "registry-provision"
    git -C "$work/repo" config user.email "registry-provision@users.noreply.github.com"
    git -C "$work/repo" add metadata/_platform
    git -C "$work/repo" commit -m "Add platform snapshots for $project_id"
    git -C "$work/repo" push origin HEAD
fi

gh api -X PUT "/orgs/$ORG/teams/$squad_team/repos/$full" -f permission=maintain

team_id() { gh api "/orgs/$ORG/teams/$1" --jq .id; }
squad_id="$(team_id "$squad_team")"
release_id="$(team_id "$PLATFORM_RELEASE_TEAM")"
for target in dev test uat preprod prod; do
    case "$target" in
        preprod | prod) reviewers="[{\"type\":\"Team\",\"id\":$release_id},{\"type\":\"Team\",\"id\":$squad_id}]" ;;
        *) reviewers="[{\"type\":\"Team\",\"id\":$squad_id}]" ;;
    esac
    printf '{"reviewers":%s,"deployment_branch_policy":{"protected_branches":true,"custom_branch_policies":false}}' "$reviewers" \
        | gh api -X PUT "/repos/$full/environments/$target" --input -
done

gh api -X POST "/repos/$full/rulesets" --input "$registry/rulesets/default-branch.json" \
    || echo "ruleset not created (it may already exist)"
echo "provisioned $full"
