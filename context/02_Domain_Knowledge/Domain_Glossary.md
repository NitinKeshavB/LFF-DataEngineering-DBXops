# Domain Glossary — cookie-cutter-dbx

**This is the highest-ROI file in the repository, and it starts empty.**

One agreed name per concept, with the synonyms to avoid. Without it, the same idea
quietly becomes `user`, `account`, `customer` and `principal` across four modules, and
every reader afterwards pays for it. Retrofitting consistent vocabulary onto a package
that already has public API is expensive; adding a row here costs a minute.

Fill it in as soon as this package has its first domain concept — not later.

## How to use it

1. One row per term. Canonical name in **Title Case**.
2. `(use)` marks an acceptable synonym; `(avoid)` marks one that is forbidden.
3. The canonical name is what appears in code, docstrings, errors and commit messages.
4. For a term that needs more than a sentence, add `Glossary_Details/<term-slug>.md`
   and link it here.
5. When you need a term that is missing, **add the row rather than coining usage
   ad hoc.**

Under the `full` doctrine this file is binding: canonical terms only, and any domain
term used must be cited back to a row here (`CLAUDE.md` §11). Under `lean` it is
advisory but still read.

## Terms

| Term | Definition | Synonyms / Notes |
|---|---|---|
| | | |

<!-- Example rows from a hypothetical order-management package — delete these and
     write your own:

| Order | A confirmed customer request to purchase goods | (use) Purchase Order · (avoid) Cart, Request |
| Invoice | A document issued to a customer requesting payment | (use) Bill · (avoid) Receipt |
| Customer | A person or organisation that purchases | (avoid) User, Client, Account |
-->

## Naming conventions

Project-wide conventions that are not single terms:

- Public functions and modules: `snake_case`. Classes: `PascalCase`.
- A leading underscore (`_name`) means internal — not part of the public API (`CLAUDE.md` P0 #5).
- Package exceptions: name them `<Something>Error`.
- Boolean parameters read as a question: `include_archived`, not `archived_flag`.
