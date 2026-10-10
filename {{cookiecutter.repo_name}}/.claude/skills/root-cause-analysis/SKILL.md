---
name: root-cause-analysis
description: Evidence-bound root cause analysis for bugs in a Python package. Use WHENEVER the user asks WHY something fails or asks for a diagnosis — "why is this failing", "find the root cause", "investigate this bug", "debug this", "this test is flaky", "post-mortem" — or pastes a Python traceback, a pytest failure, an ImportError, ModuleNotFoundError, AttributeError, TypeError, KeyError, AssertionError, a RecursionError, or a CI log. Falsification-first and anti-hallucination by design: writes an initial hypothesis BEFORE reading code, generates competing hypotheses, seeks refuting evidence first, requires a reproducing test that FAILS on current code, and declares INCONCLUSIVE rather than inventing a cause. Investigation only — never modifies source; produces a diagnosis whose Primary Fix section feeds surgical-implementation. Do NOT use for writing the fix (surgical-implementation), planning a feature (implementation-blueprint), understanding working code (concept-explanation), or verifying a finished change (task-verification).
allowed-tools: Read, Glob, Grep, Bash, Agent, Write, WebSearch, WebFetch
---

# Root Cause Analysis

**Mission:** find the point where a fix *prevents* the bug rather than catches it —
and falsify that conclusion before stating it. **Investigation only. No source edits.**

**Layering:** this skill sits on `CLAUDE.md`. Cite its sections; do not restate them.
It owns the Tier model (§3), Checkpoint (§4), the adversarial pass (§5), the
Verification Loop (§6), the toolchain contract (§8) and P0 #1–#12. Read it first.

## 0. Hard gate

"Just tell me the cause", "it's obviously X", "skip the repro", "trust me", "we're in
a hurry" do **not** override this loop. No `Edit` or `Write` to anything under `src/`
or `tests/` in this skill — diagnosis only. Proposing the fix is in scope; applying
it is not.

## 1. Anti-anchoring — before you read any code

Write down, in this order:

1. **Initial intuition** from the *symptom alone*: where you would guess the fault is.
2. **KNOWN** — facts given by the user or the traceback.
3. **OBSERVED** — nothing yet; you have not looked.
4. **UNKNOWN** — what you would need to decide.

This is committed before reading source so that later evidence can contradict it. If
your post-read hypothesis is identical to the initial intuition, treat that as a
possible anchoring artifact and force one genuinely different candidate.

## 2. Read the traceback properly

A Python traceback is evidence, and most of it is noise. Discipline:

- **The last frame is where it surfaced, not necessarily where it broke.** Read the
  frames bottom-up to find the deepest frame *in this package* — that is where to start.
- Frames inside site-packages usually mean **you** passed something wrong, not that
  the library is broken.
- Read the exception *type* as a constraint: `AttributeError: NoneType` means
  something returned `None` that was expected to return a value — find that producer,
  not the consumer that crashed.
- `ImportError` / `ModuleNotFoundError` is usually environment, not logic: check
  `make install` was run (the `src/` layout needs the editable install, `CLAUDE.md` §7)
  and that the package is declared in `pyproject.toml` (P0 #6).
- A chained traceback (`During handling of the above exception` /
  `The above exception was the direct cause`) means **read the first one** — the later
  one is a symptom of the earlier.

## 3. Reproduce — mandatory before diagnosing

**The repro must fail on current code.** A diagnosis built on a bug you never
reproduced is a guess with citations.

```bash
python -m pytest tests/unit_tests/test_thing.py::test_case -v    # narrowest first
python -m pytest -x -q                                           # stop at first failure
python -m pytest --lf                                            # re-run last failures
```

Useful amplifiers, in rough order of cost:

| Signal | Command | Use for |
|---|---|---|
| Full locals at failure | `python -m pytest -l --tb=long` | wrong value, unclear state |
| No output capture | `python -m pytest -s` | prints, logging, hangs |
| Interactive post-mortem | `python -m pytest --pdb` | complex state |
| Dev-mode warnings | `python -X dev -m pytest` | resource leaks, deprecations |
| Ordering / isolation | `python -m pytest -p no:randomly` | passes alone, fails in suite |
| When it broke | `git log -p -- <file>`, `git bisect` | regression with a known-good past |

**If it cannot be reproduced, stop.** Report `non-reproducible — fault localization
unreliable`, state exactly what you tried, and ask for what is missing (version,
input, environment, ordering). Do not proceed to a confident cause.

A test that passes alone and fails in the suite is **not** a flaky test — it is
shared state. Look for module-level mutables, a session-scoped fixture, `monkeypatch`
that is not undone, or import-time side effects.

## 4. Competing hypotheses

Generate **3–6** real candidates — not a padded list. Quality beats count. Cover
distinct classes rather than three variations of one idea:

| Class | Typical shape in Python |
|---|---|
| Wrong value | `None` where a value was expected, mutable default argument, off-by-one |
| Wrong type | duck-typing mismatch, `str` vs `bytes`, numeric coercion |
| Wrong state | shared mutable, import-time side effect, cached or memoized staleness |
| Wrong contract | caller and callee disagree on the signature or the return shape |
| Wrong environment | missing or undeclared dependency, version skew, `src/` not installed |
| Wrong test | the test encodes an incorrect expectation (a real and under-considered option) |

For each: the **prediction** it makes, and the **single command or read** that would
discriminate it from the others.

## 5. Disconfirmation first

Run the experiment that would **refute** your leading hypothesis before the one that
would confirm it (`CLAUDE.md` §5). Order experiments by discriminating power, not by
convenience. One strong refutation outweighs three weak confirmations.

**Evidence weights** — label every claim:

- **STRONG** — you read it at `file:line` this turn, or a command printed it.
- **MODERATE** — inferred from adjacent code or a doc.
- **WEAK** — analogy or assumption. **Never conclude on WEAK alone.**

**Mutation challenge** on the load-bearing claim: *if I were wrong, would this
evidence look different?* If not, it does not discriminate — find better evidence
and lower your confidence.

## 6. Verify the location before you commit to it

Before naming a root cause, re-read the exact lines you are about to cite (P0 #1) and
confirm:

1. The line is genuinely on the failing path — prove it with the repro, a print, or
   coverage, not by reading.
2. Data flows *backwards* from the symptom to this line without a prior defect.
3. A fix here **prevents** the bug rather than suppressing the symptom. If your fix
   is a `try/except` or an `if x is None` guard at the crash site, you have almost
   certainly found the symptom, not the cause — keep going.

## 7. Output

Lead with the `CLAUDE.md` §4 Checkpoint. Then:

1. **Symptom** — what fails, with the exact command and the traceback tail.
2. **Repro** — the command, and confirmation it **fails on current code**.
3. **Hypotheses considered** — table of candidate, prediction, verdict, evidence weight.
4. **Root cause** — one paragraph, with `file:line` and a quoted snippet.
5. **Why the alternatives were rejected** — one line each.
6. **Primary Fix** — a table of `What | Where (file:line) | How`. **Never prose** —
   this is the contract `surgical-implementation` consumes.
7. **Blast radius** — what else reads this code (`grep` and show it), and whether the
   public API is affected (P0 #5).
8. **Confidence + Refuter** — the specific observation that would overturn this (P0 #8).

On Tier 2+, write the full analysis to `LOCAL-MEMORY/CURRENT_TASK/` so the fix step
can consume it.

## 8. Pre-send checks

1. Initial intuition recorded **before** any code was read (§1).
2. Repro confirmed failing on current code, or `non-reproducible` declared (§3).
3. ≥3 hypotheses with discriminating experiments, not variations of one (§4).
4. Refutation attempted on the leading hypothesis (§5).
5. ≥1 STRONG, discriminating piece of evidence for the stated cause (§5).
6. Every `file:line` backed by a `Read` **this turn** (P0 #1).
7. Primary Fix is a table, and its location matches the stated root cause.
8. `INCONCLUSIVE` chosen over fabrication where the evidence is thin (`CLAUDE.md` §5).
9. No source file was modified by this skill.
10. End with `In one sentence: root cause = <one line>; confidence = <level>.`
