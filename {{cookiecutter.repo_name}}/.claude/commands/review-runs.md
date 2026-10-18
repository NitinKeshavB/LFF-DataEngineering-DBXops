---
description: "Summarise how a project's pipelines have run: failures, SLA misses, cost movers and ranked open recommendations. Triggers: /review-runs, \"how did the runs go\", \"what is costing us\"."
argument-hint: "<project> [--since 7d]"
allowed-tools: Read, Glob, Grep, Bash, AskUserQuestion
---

# /review-runs

Read-only. Summarise `$ARGUMENTS` (a project id, and `--since`, default `7d`) from the `platform_insights` views of the target the user names.

**Never choose a Databricks profile.** Ask the user which profile and which target (`conf/environments.yml`) to use, and pass `--profile <name>`.

Query these views (they are built in Phase 4; until they exist, fall back to `platform_meta`'s `md_run`, `md_task_run`, `md_pipeline_update` and
`md_run_performance`, and say so):

| Section | View | Report |
|---|---|---|
| Failures | `v_job_health` | each failed run, its failing task, termination code and the commit and PR that deployed it; the likely cause in one sentence, labelled as a guess |
| SLA misses | `v_job_health` | runs over the duration threshold, with p50 and p95 |
| Cost movers | `v_cost_daily`, `v_burn_rate`, `v_cost_per_row` | the five biggest changes against the 7 and 28 day baselines, untagged spend, projected month-end |
| Recommendations | `v_recommendations` | open items ranked by expected saving, each with its evidence, severity and status |

Finish with the three actions that would help most. Do not apply any change; a person decides, and the answer is recorded with
`python -m dbxops.metastore decide` (added in Phase 5; until then note the answer in the PR).
