# Onboarding a squad

The registry (`templates/registry-repo`, deployed as `dbx-platform-registry`) is the only way in. See its README for the full flow.

1. The squad opens the **Onboard a project** issue form: project id, repository kind (`domain` or `project`), squad and GitHub
   team, data domain, technical / data / executive owner and data steward emails, support contact, service principal,
   tags (`cost_center`, `region`, ...), optional repository name. AD groups are **derived**: `AAD-DBX-<PROJECTID>-<DEV|READ|ADMIN>`
   (project id upper-cased, underscores removed).
2. `registry-intake` validates it (all problems reported together; hostile text is rejected as data) and opens a pull request.
3. The platform team reviews and merges: **the approval**.
4. `registry-provision` creates the private repository from this template, writes `metadata/_platform/`, grants the squad's team
   `maintain` (never `admin`, so it cannot bypass the ruleset), creates the five GitHub environments (the platform release team and
   the squad review `preprod` and `prod`), and applies the default-branch ruleset.
5. The squad runs `make onboard-check` (strict placeholder check) and `make new-bundle NAME=<n> DOMAIN=<data-domain-path>`.

A **change** (new owner, new tag value) goes through the *Change a project* form the same way and replaces the registration.

## Proposed privilege matrix (not yet applied by any code)

| Principal | dev | test / uat / preprod | prod |
|---|---|---|---|
| Platform service principal | owns the catalog; CREATE SCHEMA | same | same |
| Project service principal | owns its `<project>_*` schemas; all writes | owns schemas; deploy/run writes | owns schemas; deploy/run writes |
| `developers` | USE, SELECT, MODIFY, CREATE TABLE/FUNCTION/MODEL/VOLUME | USE, SELECT | USE, SELECT on `_gold` only |
| `readers` | USE, SELECT | USE, SELECT | USE, SELECT on `_gold` only |
| `admins` | MANAGE | MANAGE | MANAGE, no MODIFY |

No squad principal gets CREATE SCHEMA on any catalog.
