---
artifact_type: LESSON
producer: /implement
state: verified
parent_artifact: null
created: 2026-10-10T00:00:00Z
tier: 3
verification:
  command: "make lint-ci"
  expected: "all hooks Passed/Skipped"
  executed: true
---

# pre-commit vs. system git 2.25.1

**What happened:** `make install` installed the unpinned `pre-commit` 4.6.2. It calls
`git ls-files --deduplicate`, which needs git >= 2.31, so `make lint` stopped with exit 3.

**What I tried first:** to find the first bad version I searched only `pre_commit/git.py`. That search
said versions up to 4.6.1 were clean, so I pinned `<4.6.2`. 4.6.1 failed the same way: the call lives in
a different module.

**Signal I missed:** I scoped the search to the file I *expected* to hold the call, not to the whole
package.

**Fix:** `pre-commit<4.6.1` in `[project.optional-dependencies].static-code-qa`. A recursive grep
over every 3.8.0–4.6.0 wheel finds no `deduplicate`. Drop the pin once system git is >= 2.31.

**Rule:** when bisecting a dependency for a symbol, grep the whole unpacked package, and confirm
the result by installing the chosen version and running the failing command.
