# Job Sultan Project Instructions

## Project direction

Before reviewing or changing this repository, read:

- `docs/GENERAL_DIRECTION.md`

The General Direction document is the master build-out plan for this project.

## Core platform requirements

- This is a job-board platform serving six Gulf countries.
- Identify and preserve the existing six-country architecture.
- Do not hardcode the platform to Saudi Arabia.
- WUZZUF Saudi Arabia is one source configuration, not the entire platform.
- The system must remain capable of supporting multiple job sources and countries.
- Preserve existing filtering, search and country logic unless a change is clearly necessary.
- Do not assume a database field means the corresponding frontend or backend feature is complete.

## Architecture rules

- Inspect the existing architecture before proposing changes.
- Prefer small, staged changes over major rewrites.
- Do not replace frameworks, rename database fields or restructure unrelated files without approval.
- Preserve the existing database schema wherever reasonably possible.
- Keep job importing, enrichment, normalisation and frontend display clearly separated.
- Job cards should open internal job-detail pages.
- External application URLs should be used by the Apply button.
- Do not modify Render or deployment configuration unless explicitly instructed.

## Working rules

- Explain the current architecture before making substantial changes.
- State which files will change before editing.
- Run syntax checks and available tests after changes.
- Report every changed file and explain its purpose.
- Do not commit, push, merge or delete files without explicit approval.
- Never expose or commit secrets, API keys, passwords or environment variables.
