# Changelog

This file records changes in each release.

## Unreleased

### Added

- The `--pages` option selects physical PDF pages and ranges.
- The `--split always` option divides facing-page scans into left and right halves.
- Reports include `selected_pages`, `page_count`, `half`, and `crop`.
- The repository includes the AGPL-3.0-or-later license.
- `SECURITY.md` describes supported versions and private vulnerability reporting.
- CI runs lint, type checks, non-model tests, and `uv build` on macOS.
- Dependabot checks `uv` and GitHub Actions every week.
- CodeQL scans Python on pushes, pull requests, and a weekly schedule.
- Contributor guidance, a code of conduct, and a bug report template support contributions.
- `save_tts_markdown` saves reviewed Markdown as `<name>.tts.md` without changing the extraction.

### Changed

- FastMCP is 4.1.4, and ty is 0.0.92.
- Locked transitive dependencies use their current compatible versions.
- The CodeQL workflow uses `github/codeql-action` v4.39.1.
- CI no longer passes an unsupported `setup-uv` input.
- The JSON report uses schema version 2.
- `read_markdown` accepts sections up to 65,536 UTF-8 bytes.
- Provenance boxes and crop rectangles use the top-left page origin.
- The README explains that an MCP client can forward returned Markdown to a remote provider.

### Breaking Changes

- Report schema version 2 replaces schema version 1.
- Provenance boxes use the top-left origin instead of the bottom-left origin.
- `PDF2MD_MCP_ROOT` is required. The server no longer uses the current directory.

## 0.2.0 - 2026-10-25

### Added

- The optional `mcp` extra installs FastMCP 4.1.3.
- The `pdf2md-mcp` command serves two local tools over stdio.
- `convert_pdf` reuses the conversion pipeline and always writes a report with schema version 1.
- Conversion summaries include paths, page counts, timing, and at most 20 distinct warnings.
- `read_markdown` returns bounded UTF-8 chunks through temporary conversion IDs.
- The server checks root containment, symlinks, output conflicts, and changed Markdown files.
- A process lock rejects concurrent conversions across server instances.
- Tests cover MCP schemas, publication failures, checkpoint recovery, stdio output, and optional installation.
- Separate smoke tests exercise Apple Vision OCR and MLX through MCP worker threads.

### Limits

- The server supports stdio only.
- The root restriction does not provide an operating-system sandbox.
- Client timeouts do not guarantee cancellation.
- The server provides no background tasks or progress notifications.

## 0.1.0 - 2026-10-25

Initial release for Apple Silicon macOS with Python 3.12.

### Added

- The command line interface (CLI) converts one PDF into Markdown with local engines.
- Fast mode selects native extraction or Docling with Apple Vision optical character recognition (OCR).
- Best mode uses GraniteDocling through MLX for nonblank pages.
- Automatic routing retries failed vision language model (VLM) pages with standard Docling.
- Source and engine options support manual selection.
- Output includes extracted figures and an optional JSON report with schema version 1.
- Reports record page provenance, extraction attempts, warnings, settings, and engine versions.
- Checkpoints preserve completed chunks after interruption.
- Failed conversion preserves the previous output.
- Cached models support offline conversion.
- Tests cover routing, validation, assets, recovery, and output protection without model downloads.
- Evaluation tools record runtime, peak memory, and conversion failures.

### Known limitations

- Fast mode can omit footnotes.
- Best mode can place numeric values in incorrect table rows.
- Heading levels, equations, and reading order require source checks.
- Ambiguous paragraphs and tables remain separate across chunk boundaries.
- GraniteDocling primarily targets English documents.
- Validation cannot prove that an extraction matches its source.

See the [README](README.md) for installation and use.
See the [evaluation report](EVALUATION.md) for measurements and observed failures.
