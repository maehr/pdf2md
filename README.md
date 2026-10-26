# pdf2md

[![CI](https://github.com/maehr/pdf2md/actions/workflows/ci.yml/badge.svg)](https://github.com/maehr/pdf2md/actions/workflows/ci.yml)

Convert one PDF into Markdown with local engines.
Version 0.3.0 adds an MCP tool for agent-reviewed TTS Markdown.
The default fast mode uses native text and Apple Vision optical character recognition (OCR) through Docling.
Best mode uses the GraniteDocling vision language model (VLM) through MLX on Apple Silicon.

See the [changelog](CHANGELOG.md) for release notes.

## Requirements

- A Mac with Apple Silicon and macOS.
- Python 3.12.
- `uv` for dependency management.
- Network access for dependency installation and initial model downloads.

The package name is `pdf2md-local`.
The command name is `pdf2md`.
Each invocation processes one PDF.

## Install

From the project directory, install the locked dependencies:

```sh
uv sync --locked
```

The first run for an engine downloads its required models into the standard Hugging Face cache.
Later runs reuse cached weights.
All inference runs locally.
The application disables cloud inference.
The command does not send PDF content to a remote service.
An MCP client can forward Markdown returned by `read_markdown` to a remote provider.
See [Local MCP server](#local-mcp-server).

## Convert

```sh
uv run --locked pdf2md paper.pdf --report
uv run --locked pdf2md book.pdf --quality best
uv run --locked pdf2md scan.pdf --source scan -o output/book.md
uv run --locked pdf2md book.pdf --pages 12-40 -o chapter.md
uv run --locked pdf2md book.pdf --pages 1,3,7-9 -o sample.md
uv run --locked pdf2md scan.pdf --pages 12-40 --split always --report -o chapter.md
uv run --locked pdf2md --help
```

The default output is `paper.md` beside `paper.pdf`.
Figures use `paper.assets/`.
The optional report uses `paper.report.json`.
Custom output paths determine the asset and report names.

| Option | Values | Default |
| --- | --- | --- |
| `-o`, `--output` | Path to the Markdown output | Beside the input, with `.md` |
| `--source` | `auto`, `digital`, `ocred`, `scan` | `auto` |
| `--quality` | `fast`, `best` | `fast` |
| `--engine` | `auto`, `pdf-inspector`, `docling`, `vlm` | `auto` |
| `--pages` | Single pages, inclusive ranges, or comma-separated selections | All pages |
| `--split` | `none`, `always` | `none` |
| `--report`, `--no-report` | Enable or disable the JSON report | Off |
| `--help` | Show command help | Off |

The JSON report uses schema version 2.
It records the input hash, settings, engine versions, timing, warnings, and page provenance.
Each page record includes its classification, engine, OCR use, VLM use, and extraction attempts.
The `page_count` field records the input page count.
The `selected_pages` field records the selected physical pages.

Page selection uses physical PDF page numbers, starting at 1.
The command removes duplicate selections and processes pages in source order.
It rejects empty, malformed, reversed, and out-of-range selections before inference.

Use `--split always` for scans with two facing pages on each PDF page.
The command divides each selected page vertically at its midpoint.
It processes the left half before the right half.
Split conversion renders each half at 144 DPI.
Automatic split conversion uses Docling in fast mode and MLX in best mode.
Explicit `--engine pdf-inspector` cannot process split pages.

Each half has a separate report record with its original page number, `half`, and `crop` rectangle.
Crop rectangles and provenance boxes use PDF points on the displayed source page, with the origin at the top left.
Rectangle values follow the order `[left, top, right, bottom]`.

The command prints progress and warnings to standard error.
It prints the output path to standard output after success.

## Local MCP server

Install the optional FastMCP 4.1.4 server:

```sh
uv sync --locked --extra mcp
```

Start the server with one allowed directory:

```sh
PDF2MD_MCP_ROOT="$HOME/Documents" uv run --locked --extra mcp pdf2md-mcp
```

This command starts a stdio protocol server.
It does not provide an interactive terminal prompt.
`PDF2MD_MCP_ROOT` is required.
The server exits with an error if the variable is unset or empty.
The root must exist before startup.
The server provides no HTTP listener.

The server exposes three tools:

| Tool | Behavior |
| --- | --- |
| `convert_pdf` | Converts one local PDF and returns paths, page counts, timing, and bounded warnings. |
| `read_markdown` | Reads up to 65,536 UTF-8 bytes from a successful conversion. |
| `save_tts_markdown` | Saves reviewed Markdown as `<name>.tts.md` next to the extracted Markdown. |

`convert_pdf` accepts `input_pdf`, `output_markdown`, `source`, `quality`, `engine`, and `overwrite`.
Source, quality, and engine values match the CLI options above.
Relative paths start at the configured root.
The default output replaces the input suffix with `.md`.
The tool always writes a report with schema version 2.
It returns at most 20 distinct warnings and counts omitted warnings.
It preserves the existing pipeline, checkpoints, and atomic publication.

Existing Markdown, report, or asset targets require `overwrite=True`.
This option can replace existing output files after successful conversion.
A work directory alone permits checkpoint recovery without `overwrite=True`.
Failed conversion preserves prior published outputs.
One process runs one conversion at a time.
A second conversion receives a busy error.

Use the returned `conversion_id` with `read_markdown`.
Start at `offset_bytes=0`.
Use `next_offset_bytes` for each later call until `eof` is true.
Set `max_bytes` between 4 and 65,536.
Offsets count UTF-8 bytes, not characters.
Conversion IDs expire when the server process exits.
The server rejects changed or missing Markdown files.

Use `save_tts_markdown` with the same conversion ID and the reviewed Markdown.
The tool writes `<name>.tts.md` next to the extracted `<name>.md`.
It never changes the extracted Markdown.
The server does not rewrite, summarize, or normalize the text.
The conversion output directory must still exist.
Existing TTS copies require `overwrite=True`.
The reviewed text must not exceed 16 MiB.
The tool writes the new file atomically.

### Client configuration

Replace both placeholder paths with absolute paths:

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

The client starts the server as a local subprocess.
Configuration keys can differ between MCP clients.
Remove `HF_HUB_OFFLINE` until the required model weights exist in the cache.
After that, use `HF_HUB_OFFLINE=1` for offline operation.

### File access and limits

The configured root limits tool access to one directory tree.
The server rejects URLs, paths outside the root, and symlinks in file paths.
It also checks report, asset, and work targets.
`read_markdown` accepts conversion IDs, never file paths.
`save_tts_markdown` derives its target from the conversion ID.
The root restriction is an application check, not an operating-system sandbox.
The server retains the local user's file permissions.

All PDF processing and inference run locally.
The first conversion can download required model weights.
The server returns Markdown to the MCP client.
The client can forward that Markdown to a remote provider.
The application cannot guarantee end-to-end privacy after content reaches the client.
Review the data policy of your client before you convert sensitive documents.
The server sends diagnostics to standard error.
Standard output contains only MCP protocol messages.
Long conversions remain synchronous tool calls.
Client timeouts do not guarantee cancellation.
This release provides no background tasks or progress notifications.

## Engine selection

Fast mode uses native extraction for pages with reliable text and simple layouts.
It uses standard Docling for OCR and complex layouts.

Best mode uses MLX for every nonblank page.
Automatic routing retries failed VLM pages with standard Docling.
Fast mode avoids all VLM inference, including formula enrichment.

Explicit engine selection disables engine fallback.
The combination `--engine vlm --quality fast` is invalid.
Explicit VLM selection requires `--quality best`.

The source override changes classification.
The `ocred` value identifies a scan with an existing OCR text layer.
The `scan` and `ocred` overrides force OCR when standard Docling processes a page.

## Recovery and offline use

Conversion processes eight pages at a time.
Page selection limits each chunk to eight selected source pages.
Split checkpoints require both halves of every source page in the chunk.
A hidden work directory stores completed chunks beside the output.
Repeat the same command after interruption to resume.

Changes to the input, settings, models, or engine versions invalidate previous checkpoints.
Settings include page selection and splitting.
The report counts resumed source pages after both halves are complete.
Successful conversion removes its checkpoints.
Failed conversion preserves the previous Markdown output.
Existing output files are replaced after successful conversion.

After the required models exist in the cache, disable Hugging Face network access:

```sh
HF_HUB_OFFLINE=1 uv run --locked pdf2md paper.pdf
```

## Fidelity limits

The engines supply document structure, tables, captions, and figures when available.
The application preserves engine output without summarization.
It removes confirmed running headers, footers, and page numbers.
It preserves unconfirmed footer text, including references near the page edge.
It preserves ambiguous content and reports warnings.
Native extraction also saves embedded images.

The checks detect missing pages, invalid text, and broken asset links.
They cannot prove that a transcription matches its source.
Inspect important quotations, formulas, reading order, and table continuations against the PDF.
Ambiguous boundaries between chunks remain separate.
The application does not infer missing chapter levels or repair equations.

Fast mode can omit footnotes.
Best mode can place numeric values in incorrect table rows.
Both modes can assign incorrect heading levels.
See the [evaluation report](EVALUATION.md) for measured results, review methods, and known failures.

GraniteDocling primarily targets English documents.
Evaluate other languages against source pages before relying on the output.

## Development

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv run --locked pytest
uv run --locked pytest -m integration
```

The normal test suite does not download model weights.
The CI workflow runs these non-model checks on macOS.

Run the MCP checks with the optional extra:

```sh
uv run --locked --extra mcp ruff check .
uv run --locked --extra mcp ruff format --check .
uv run --locked --extra mcp ty check
uv run --locked --extra mcp pytest
uv run --locked --extra mcp pytest --doctest-modules src tests
```

Tests without the extra skip MCP integration tests.
The tests still check CLI independence and the missing-extra error.

Include docstring examples with this command:

```sh
uv run --locked pytest --doctest-modules src tests
```

Run the separate model smoke test after the required weights exist in the cache:

```sh
HF_HUB_OFFLINE=1 uv run --locked pytest -m models
```

Test Apple Vision OCR and MLX through MCP worker threads separately:

```sh
HF_HUB_OFFLINE=1 uv run --locked --extra mcp pytest -m models tests/test_mcp_server.py
```

## Release checks

1. Confirm that `pyproject.toml`, `uv.lock`, and `src/pdf2md/__init__.py` contain the same project version.
2. Update the [changelog](CHANGELOG.md) with the version and release date.
3. Run the development checks above.
4. Build the source distribution and wheel:

   ```sh
   uv build
   ```

5. Inspect the archives in `dist/` for the expected package version and current README metadata.
6. Check that the archives exclude evaluation data, caches, and model weights.

## Evaluate

```sh
uv run --locked python scripts/evaluate.py paper.pdf book.pdf --output evaluation
uv run --locked python scripts/make_samples.py
uv run --locked python scripts/evaluate.py evaluation/samples/book.pdf --engine pdf-inspector --output evaluation/long-book
```

The script runs all three engines in separate processes.
It records wall time, peak memory, failures, and report paths.
Use representative papers, scans, and books.
Include a book with hundreds of pages for memory and recovery checks.
Review each output against the source before recording a fidelity result.

See the [evaluation report](EVALUATION.md) for measured results and known failures.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before a large change.
Follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Report vulnerabilities through the private channel in [SECURITY.md](SECURITY.md).

## License

This project is licensed under the GNU Affero General Public License, version 3 or later (AGPL-3.0-or-later).
See [LICENSE](LICENSE).
