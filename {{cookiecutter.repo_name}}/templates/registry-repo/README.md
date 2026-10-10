# dbx-platform-registry

The platform's register of squads: who they are, who owns what, which data domain they work in, and the tags every
project must carry. Registering here is how a squad gets a repository and workspace access.

| Path | What it is |
|---|---|
| `projects/<project_id>.yaml` | one registration per squad: owners, AD groups, service principal, tags, repository |
| `domains.yaml` | the domain taxonomy: Business Domain > Sub-domain > Data Domain; a domain path joins the slugs with `__` |
| `required_tags.yaml` | tag keys every registration must define (values are free-form) |
| `settings.yaml` | platform-wide settings written into each project's `metadata/_platform/repo.yaml` |
| `glossary/*.ttl` | the shared SKOS glossary; squads propose terms by pull request |
| `.github/ISSUE_TEMPLATE/` | the onboarding and change request forms |
| `.github/workflows/` | intake, validate, provision and publish |
| `scripts/provision_project.sh` | creates and configures a squad's repository |

## The flow

1. A squad opens the **Onboard a project** issue form.
2. `registry-intake` validates the request and opens a pull request adding `projects/<project_id>.yaml`
   (or tells the requester what is wrong).
3. `registry-validate` checks the pull request. The platform team reviews and merges it; **this is the approval**.
4. `registry-provision` creates the squad's private repository from the template, writes its `metadata/_platform/`
   snapshots, grants its GitHub team access, creates the `dev`..`prod` environments (the platform release team and the
   squad review `preprod` and `prod`), and applies the default-branch ruleset.
5. `registry-publish` writes the platform-owned records into `platform_meta`.
6. Workspace setup (schemas, grants, tags) runs behind an approval in the platform repository.

## Needs before the first run

Repository variables `ORG`, `TEMPLATE_REPO`, `DBXOPS_VERSION`, `APP_ID`, `PLATFORM_RELEASE_TEAM`; secret
`APP_PRIVATE_KEY` for a GitHub App with repository-administration rights in the organisation; the AD groups created
by IAM; and per-target environments carrying `DEPLOY_SP_CLIENT_ID`. Replace the team in `CODEOWNERS`.

None of these workflows has run against a real organisation yet.
