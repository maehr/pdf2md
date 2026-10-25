# FastMCP integration specification

Status: Proposed
Target repository: [maehr/pdf2md](https://github.com/maehr/pdf2md)
Framework: [PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp)
Baseline: pdf2md `main` at `0aaf68bfc5d7cfdf2d8b15011e7b111784c1b577`; FastMCP `4.1.3`.

## 1. Objective

Add a local Model Context Protocol (MCP) server to `pdf2md-local`.
Use FastMCP to expose the existing PDF conversion pipeline to AI clients.
Keep the existing `pdf2md` command and conversion behavior unchanged.

The server MUST:

- Convert one local PDF per tool call.
- Reuse `pdf2md.pipeline.convert` and existing `Settings` and `Paths`.
- Preserve engine routing, OCR, VLM fallback, checkpoints, assets, and atomic publication.
- Return structured conversion metadata.
- Let a client read bounded sections of the generated Markdown.
- Run over standard input/output (stdio) only.
- Restrict file access to one operator-configured directory.
- Keep all PDF processing and inference local.

## 2. Existing contract

The current implementation has these interfaces:

| Component | Existing contract |
| --- | --- |
| `src/pdf2md/cli.py` | Typer command `pdf2md`. |
| `src/pdf2md/pipeline.py` | `convert(Paths, Settings, *, write_report=False, progress=None) -> ConversionReport`. |
| `src/pdf2md/models.py` | `Paths`, `Settings`, `ConversionReport`, and literal source, quality, and engine types. |
| `src/pdf2md/engines.py` | Native extraction, Docling OCR, and MLX VLM. |
| `src/pdf2md/core.py` | Pure routing, text checks, and asset validation. |

`Settings` accepts:

- `source`: `auto`, `digital`, `ocred`, or `scan`. Default: `auto`.
- `quality`: `fast` or `best`. Default: `fast`.
- `engine`: `auto`, `pdf-inspector`, `docling`, or `vlm`. Default: `auto`.

The pipeline publishes `<stem>.md`, optional `<stem>.report.json`, and extracted `<stem>.assets/`.
It processes eight pages per checkpoint and resumes completed chunks.
It preserves prior outputs after conversion failure.
Its report schema version is `1`.

**Do not duplicate this pipeline in the MCP layer.**

## 3. Scope

### In scope

- An optional FastMCP dependency.
- A `pdf2md-mcp` console entry point.
- Two MCP tools: `convert_pdf` and `read_markdown`.
- Local path validation and safe overwrite behavior.
- Structured Pydantic response models.
- MCP tests and client configuration documentation.

### Out of scope

- HTTP, SSE, or public network transports.
- Authentication, remote uploads, URLs, and cloud conversion.
- PDF editing, batch jobs, and directory watching.
- MCP resources, prompts, or background task extensions.
- Changes to extraction, routing, report schema, or checkpoint format.
- Automatic transcription repair or quality claims.

## 4. Dependency and entry point

Add FastMCP as an optional extra in `pyproject.toml`:

```toml
[project.optional-dependencies]
mcp = ["fastmcp==4.1.3"]

[project.scripts]
pdf2md = "pdf2md.cli:app"
pdf2md-mcp = "pdf2md.mcp_server:main"
```

Preserve all existing dependencies and the `pdf2md` entry point.
Regenerate and commit `uv.lock` with `uv lock`.
Keep FastMCP out of the default runtime dependency set.
Do not add FastMCP to `src/pdf2md/__init__.py` or the existing CLI imports.

Use Python `3.12` and `uv`, as required by `AGENTS.md`.
Install the optional server dependencies with:

```sh
uv sync --locked --extra mcp
```

Start the server with:

```sh
PDF2MD_MCP_ROOT="$HOME/Documents" uv run --locked --extra mcp pdf2md-mcp
```

This command runs an MCP protocol server. It is not an interactive terminal prompt.
The entry point MUST run `mcp.run(transport="stdio")`.
A missing `mcp` extra MUST produce an actionable error on stderr.
It MUST NOT affect the existing `pdf2md` command.

## 5. Server construction

Create `src/pdf2md/mcp_server.py`.

Expose a factory, `create_server(root: Path) -> FastMCP`.
The factory MUST register exactly the two tools defined below.
Use `FastMCP("pdf2md", version=pdf2md.__version__)`.
Provide short server instructions that describe local-only conversion and result pagination.

The `main()` entry point MUST:

1. Read and validate `PDF2MD_MCP_ROOT` once at startup.
2. Use the current working directory if the variable is unset.
3. Build the server with the validated root.
4. Start only the stdio transport.
5. Send diagnostics to stderr, never stdout.

Import FastMCP lazily or isolate its import behind the MCP entry point.
Importing `pdf2md.cli` MUST work without the optional extra.

Register synchronous tool functions with FastMCP.
FastMCP normally executes synchronous tools in a worker thread.
Verify native extraction, Apple OCR, and MLX execution on Apple Silicon.
Use a process-wide conversion lock to prevent concurrent model workloads.

## 6. Tool: convert_pdf

Expose `convert_pdf` with this public signature:

```python
def convert_pdf(
    input_pdf: str,
    output_markdown: str | None = None,
    source: Source = "auto",
    quality: Quality = "fast",
    engine: Engine = "auto",
    overwrite: bool = False,
) -> ConversionSummary:
    ...
```

### Input behavior

- Accept one local PDF path.
- Interpret relative paths beneath the configured MCP root.
- Default the output to the input path with a `.md` suffix.
- Interpret an explicit relative output path beneath the MCP root.
- Require the output suffix `.md`.
- Reuse `Settings(source=source, quality=quality, engine=engine)`.
- Preserve the pipeline's invalid `quality=fast` and `engine=vlm` rejection.
- Call `convert(Paths(input_path, output_path), settings, write_report=True)`.
- Do not run a second extraction or rewrite the generated Markdown.

The MCP tool always writes the JSON report.
The existing CLI retains its optional `--report` behavior.

### Overwrite behavior

- Default `overwrite` to `False`.
- Reject existing Markdown, report, or asset targets unless `overwrite=True`.
- Perform this check before creating output or work directories.
- Permit resume when only the pipeline's checkpoint work directory exists.
- When overwrite is enabled, use the pipeline's existing rollback protection.
- Never delete an unrelated directory to make an output path available.

### Response

Return a Pydantic model named `ConversionSummary`.

| Field | Type | Meaning |
| --- | --- | --- |
| `conversion_id` | `str` | Random UUID for reading this result during this server process. |
| `input_pdf` | `str` | Resolved input path. |
| `output_markdown` | `str` | Published Markdown path. |
| `assets_directory` | `str \| None` | Published asset directory, if present. |
| `report_json` | `str` | Published schema-v1 report path. |
| `page_count` | `int` | Physical page count. |
| `resumed_pages` | `int` | Pages loaded from valid checkpoints. |
| `seconds` | `float` | Elapsed conversion time. |
| `warnings_total` | `int` | Count of distinct warning messages. |
| `warnings` | `list[str]` | First 20 distinct warnings, in observed order. |
| `warnings_omitted` | `int` | Number of remaining distinct warnings. |

Collect warnings from `ConversionReport.warnings` and every `PageReport.warnings`.
Deduplicate by exact text while preserving order.
Do not return the entire Markdown or page-level report in this response.
Register the `conversion_id` only after successful publication.
Return tool errors for invalid input or conversion failure.
Never return a success-shaped result after failure.

Mark the tool as writing local files.
Its annotation MUST NOT claim that it is read-only.
Document that `overwrite=True` can replace existing output files.

## 7. Tool: read_markdown

Expose `read_markdown` with this public signature:

```python
def read_markdown(
    conversion_id: str,
    offset_bytes: int = 0,
    max_bytes: int = 16384,
) -> MarkdownChunk:
    ...
```

This tool reads only Markdown from successful `convert_pdf` calls.
It MUST NOT accept arbitrary file paths.

Maintain a process-local registry from random conversion IDs to output paths.
Store the published file's size and modification metadata.
Keep the registry private to the server factory.
Do not persist the registry to disk.

### Pagination

- Require `offset_bytes >= 0`.
- Require `4 <= max_bytes <= 16384`.
- Reject offsets beyond the file size.
- Reject offsets inside a UTF-8 continuation sequence.
- Read no more than `max_bytes` from the file.
- Return the largest valid UTF-8 prefix within the byte limit.
- Never replace malformed UTF-8 with replacement characters.
- Set `next_offset_bytes` to the first unread byte.
- Set `eof` only when no unread bytes remain.
- Permit an empty result at the exact end of the file.

Return a Pydantic model named `MarkdownChunk`:

| Field | Type | Meaning |
| --- | --- | --- |
| `conversion_id` | `str` | Requested conversion ID. |
| `markdown` | `str` | Decoded Markdown fragment. |
| `offset_bytes` | `int` | Requested starting byte offset. |
| `next_offset_bytes` | `int` | Offset for the next call. |
| `eof` | `bool` | Whether the complete file was read. |

Reject unknown IDs, missing files, changed files, and symlinks.
Recheck the configured root and output path before every read.
Mark this tool as read-only.
State that conversion IDs expire when the MCP process exits.

## 8. File access and security

The server runs with the local user's filesystem permissions.
Treat MCP callers as untrusted input sources.
Do not claim that a filesystem root is an operating-system sandbox.

`PDF2MD_MCP_ROOT` defines the only allowed filesystem tree.
Resolve the root to an existing directory during startup.
Reject an invalid or non-directory root.
Never accept a root override through an MCP tool parameter.

For each request:

1. Anchor relative paths below the configured root.
2. Normalize paths with `Path.resolve()`.
3. Check containment with `Path.relative_to()`, not string prefixes.
4. Reject access outside the root, including symlink escapes.
5. Reject symlinked input and output targets.
6. Require a regular `.pdf` input file.
7. Validate the output path before invoking `convert()`.
8. Preserve the pipeline's output, report, asset, and work-directory checks.

Reject URL-like inputs and non-file schemes.
Do not expose arbitrary filesystem reading through `read_markdown`.
Do not expose an HTTP listener or public interface.

The existing engines disable cloud inference.
The first run may still download required models.
Document `HF_HUB_OFFLINE=1` for cached, offline operation.
Never log PDF text, extracted Markdown, or model prompts.

## 9. Errors and execution

Use FastMCP tool errors for user-visible failures.
Use clear messages without Python tracebacks.
Keep detailed diagnostics on stderr.

Expected error cases include:

- Missing, corrupt, encrypted, or non-PDF input.
- A path outside the configured root.
- A symlink or unsafe output target.
- Existing output without `overwrite=True`.
- Invalid `source`, `quality`, or `engine`.
- A conflicting engine and quality combination.
- Missing or invalid model weights.
- A second conversion while another is active.
- An unknown, expired, or modified Markdown result.
- An invalid UTF-8 byte offset.

Use one non-blocking process-wide conversion lock.
Reject a simultaneous conversion with a clear busy message.
Retain the pipeline's per-output `fcntl` lock.
Release the process lock in a `finally` block.

Do not invent partial-success metadata.
A failed tool call MUST preserve existing published outputs.
The pipeline's checkpoints MAY remain available for a retry.

Do not enable FastMCP background tasks in this release.
Long conversions remain synchronous MCP tool calls.
Document that client timeouts do not guarantee cancellation.
Do not promise progress notifications without implementation and tests.

## 10. Implementation files

| Path | Required change |
| --- | --- |
| `src/pdf2md/mcp_server.py` | Server factory, tools, filesystem policy, registry, and entry point. |
| `pyproject.toml` | Pinned optional FastMCP extra and `pdf2md-mcp` script. |
| `uv.lock` | Updated resolved dependency graph. |
| `tests/test_mcp_server.py` | Tool contracts, path safety, pagination, errors, and conversion integration. |
| `README.md` | Install, start, client configuration, security, and limitations. |
| `CHANGELOG.md` | Document the new optional MCP interface in the next release. |

Keep `models.py`, `core.py`, `pipeline.py`, and `engines.py` unchanged unless tests reveal a necessary defect.
Place Pydantic response models beside the MCP boundary or in a small MCP-specific module.
Do not add platform-specific imports to package initialization.

## 11. Tests

Use the existing generated PDFs from `tests/conftest.py`.
Run ordinary tests without model downloads.
Use `fastmcp.Client(create_server(root))` for in-memory MCP calls.
Use `asyncio.run()` in synchronous pytest tests, or add a pinned development-only async test dependency.

### Required cases

- Tool discovery exposes exactly `convert_pdf` and `read_markdown`.
- Input schemas expose all allowed enum values and defaults.
- A local digital PDF produces Markdown and a schema-v1 report.
- The summary contains correct paths, page count, and warning counts.
- The default output uses the input file's stem.
- Custom output paths produce matching asset and report names.
- Markdown pagination reconstructs the exact original UTF-8 bytes.
- Pagination handles multibyte Unicode at chunk boundaries.
- Unknown IDs and invalid byte offsets return tool errors.
- Relative, absolute, and nested paths obey the configured root.
- Prefix-matching sibling directories remain forbidden.
- Input and output symlink escapes are rejected.
- Invalid engine combinations fail without changing prior outputs.
- Existing output requires `overwrite=True`.
- A forced publication failure preserves previous Markdown and assets.
- A checkpoint-only work directory remains resumable.
- Parallel conversion requests cannot run the engines concurrently.
- Loading and running the original CLI requires no FastMCP extra.
- A subprocess stdio test can discover and call the MCP tools.
- Subprocess stdout contains only MCP protocol messages.

Separate Mac model smoke tests from deterministic tests.
Test Apple Vision OCR and MLX with cached model weights.
Test a long PDF for memory and checkpoint behavior.
Record real measurements before reporting performance.

Run:

```sh
uv sync --locked --extra mcp
uv run --locked --extra mcp ruff check .
uv run --locked --extra mcp ruff format --check .
uv run --locked --extra mcp ty check
uv run --locked --extra mcp pytest
uv run --locked --extra mcp pytest --doctest-modules src tests
uv build
```

Also run the existing suite without the MCP extra.
Do not silently treat skipped MCP tests as passing integration coverage.

## 12. Client configuration

Document this example in `README.md`.
Replace both placeholder paths with absolute paths.
The client starts the server as a local subprocess.

```json
{
  "mcpServers": {
    "pdf2md": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/pdf2md",
        "--locked",
        "--extra",
        "mcp",
        "pdf2md-mcp"
      ],
      "env": {
        "PDF2MD_MCP_ROOT": "/absolute/path/to/pdfs",
        "HF_HUB_OFFLINE": "1"
      }
    }
  }
}
```

Remove `HF_HUB_OFFLINE` until required model weights exist.
An MCP client must use `convert_pdf` before `read_markdown`.
Use `next_offset_bytes` to retrieve the remaining Markdown.
Client configuration keys can differ between MCP hosts.

## 13. Acceptance criteria

The implementation is complete only when all conditions hold:

1. `pdf2md` behaves as documented in the current README.
2. `pdf2md-mcp` starts locally through the optional `mcp` extra.
3. FastMCP `4.1.3` is pinned and the lockfile resolves on Python `3.12`.
4. Both tools expose stable, typed input and output schemas.
5. Conversion uses the unchanged pipeline and publishes valid artifacts.
6. Markdown can be read completely through bounded UTF-8 chunks.
7. Root restrictions, symlink checks, and overwrite protection pass tests.
8. Failed conversions preserve prior outputs and valid checkpoints.
9. The stdio transport remains free of non-protocol stdout output.
10. Ruff, format checks, ty, pytest, and package builds pass.
11. Apple Silicon OCR and MLX smoke tests pass separately.
12. README and changelog describe the feature and its limits.

## 14. Reference sources

- [pdf2md README](https://github.com/maehr/pdf2md/blob/main/README.md)
- [pdf2md repository rules](https://github.com/maehr/pdf2md/blob/main/AGENTS.md)
- [pdf2md pipeline](https://github.com/maehr/pdf2md/blob/main/src/pdf2md/pipeline.py)
- [pdf2md models](https://github.com/maehr/pdf2md/blob/main/src/pdf2md/models.py)
- [FastMCP v4.1.3 release](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.3)
- [FastMCP server guide](https://gofastmcp.com/servers/server)
- [FastMCP tool guide](https://gofastmcp.com/servers/tools)
- [FastMCP testing guide](https://gofastmcp.com/servers/testing)
