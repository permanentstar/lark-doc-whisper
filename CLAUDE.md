# CLAUDE.md

Claude agents should start from `AGENTS.md`.

`AGENTS.md` is the canonical agent entry point for this repository. It links the README, architecture document, deployment SOP, configuration rules, hard constraints, and verification commands. Do not duplicate those rules here; keeping one source of truth avoids drift.

Minimum reminders:

- Read `AGENTS.md` before making changes.
- Do not commit secrets, runtime state, logs, local absolute paths, or real environment files.
- Do not add runtime logic that writes back persistent configuration.
- Run the release gate before claiming completion:

```bash
uv run --extra dev pytest
uv run python -m compileall -q src
```
