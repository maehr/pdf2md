# Changelog

This file records changes in each release.

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
