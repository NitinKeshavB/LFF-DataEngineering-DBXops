---
artifact_type: LESSON
producer: /implement
state: verified
parent_artifact: null
created: 2026-10-10T00:00:00Z
tier: 3
verification:
  command: "PR CI: Test (Python 3.11/3.12/3.13) green"
  expected: "no 3.9/3.10 jobs; remaining test jobs pass"
  executed: false_with_reason  # confirmed only after the PR re-runs
---

# Python floor raised without checking the CI matrix

**What happened:** Phase 1 raised `requires-python` to `>=3.11` in `pyproject.toml`, but
`.github/workflows/build-test-publish.yml` still tested 3.9 and 3.10. Those two PR jobs failed.

**Signal missed:** I never searched `.github/workflows/` for Python version lists when changing the floor.
The workflow's own comment named this exact mismatch.

**Fix:** the matrix is now `["3.11", "3.12", "3.13"]`.

**Rule:** when changing `requires-python`, grep `.github/workflows/` for version lists in the same change.
