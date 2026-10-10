# Harness attribution

The agent harness in this project — `CLAUDE.md`, `.claude/commands/`,
`.claude/skills/`, `context/` and `LOCAL-MEMORY/` — is **adapted from the Claude Code
Agent Harness** by Viacheslav Tronko, used under the MIT License.

| | |
|---|---|
| Upstream project | Claude Code Agent Harness |
| Author | Viacheslav Tronko (co-author: Olena Usova) |
| License | MIT |
| Relationship | adapted for Python packaging; not a verbatim copy |

## What was adapted

The methodology is upstream's: evidence weights, named refuters, the tier model, the
checkpoint-before-code protocol, the adversarial pass, and the failure-mode registry.

What changed for this project:

- Rewritten around Python packaging — `pytest`, `pip` + `setuptools`, and the
  `black`/`isort`/`flake8`/`pylint`/`mypy`/`autoflake` pre-commit stack — rather than
  language-agnostic placeholders a human must fill in.
- Frontend patterns, ORM layers and Jira/Atlassian assumptions removed.
- Skills consolidated to five, each specific to this toolchain.
- `context/` ships **pre-seeded** with this project's real layout, toolchain and
  patterns instead of blank templates, because the generator that created this project
  already knew them.
- Doctrine weight chosen at generation time (`lean` or `full`).

## Upstream license

```
MIT License

Copyright (c) 2026 Viacheslav Tronko

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
