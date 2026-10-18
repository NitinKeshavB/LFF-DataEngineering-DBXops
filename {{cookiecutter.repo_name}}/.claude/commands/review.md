---
description: "Review the current change with the read-only code-reviewer agent. Triggers: /review, \"review my change\", \"review before I merge\"."
argument-hint: "[base ref (default origin/main)]"
allowed-tools: Read, Glob, Grep, Bash, Agent, AskUserQuestion
---

# /review

Run the **`code-reviewer`** agent on the diff between `$ARGUMENTS` (default `origin/main`) and the working tree.

1. Show `git diff --stat <base>` so the scope is visible.
2. Start the agent with the base ref and the file list. It reads the doctrine, runs the read-only checks and returns a verdict, a findings table
   and the decisions it needs from the user.
3. Put every item under **Decisions needed** to the user with `AskUserQuestion`; do not answer for them. Tell them the answers are recorded with
   `python -m dbxops.metastore decide` (added in Phase 5; until then note the answer in the PR).
4. Do not fix anything. Fixing is `/implement` or `/bug-fix`.

CI runs the same agent on each pull request and posts the report as a comment; there the decisions become a checklist that blocks the merge.
