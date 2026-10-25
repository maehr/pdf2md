# Contributing

Open an issue before a large change.
Small fixes can go directly to a pull request.

Read the [README](README.md) for install and use.
Follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Report vulnerabilities as described in [SECURITY.md](SECURITY.md).
Do not report vulnerabilities in public issues.

## Setup

Use macOS on Apple Silicon with Python 3.12 and `uv`.

```sh
uv sync --locked --extra mcp
```

The `mcp` extra installs the optional server.
The normal checks do not download model weights.

## Checks

Run all checks before you open a pull request:

```sh
uv run --locked --extra mcp ruff check .
uv run --locked --extra mcp ruff format --check .
uv run --locked --extra mcp ty check
uv run --locked --extra mcp pytest
uv run --locked --extra mcp pytest --doctest-modules src tests
uv build
```

Run model smoke tests only after the required weights exist in the cache:

```sh
HF_HUB_OFFLINE=1 uv run --locked --extra mcp pytest -m models
```

The CI workflow runs the non-model checks on macOS.

## Commits and Pull Requests

Use signed Conventional Commits.
Keep one logical change per pull request.
Update the README and changelog when behavior changes.
Wait for the required CI check before you merge.
