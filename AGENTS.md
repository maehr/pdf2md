# Repository rules

## Work

- Keep the CLI small.
- Follow the README as the project contract.
- Use Python 3.12 and `uv`.
- Install dependencies with `uv sync --locked`.
- Pin direct dependencies.
- Keep development tools outside runtime dependencies.
- Use type hints.
- Use dataclasses for internal data.
- Use Pydantic at serialized boundaries.
- Keep routing and text validation pure.
- Keep cloud inference disabled.

## Checks

Run these checks before completion:

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv run --locked pytest
```

Test core logic without model downloads.
Run model smoke tests separately.
Record real measurements before making performance claims.

## Writing and commits

Use Simplified Technical English for documentation.
Keep instructions under 20 words.
Keep descriptions under 25 words.
Keep `CLAUDE.md` as `@AGENTS.md` only.
Use signed Conventional Commits.
Ask the user to unlock 1Password if signing fails with `failed to fill whole buffer`.
