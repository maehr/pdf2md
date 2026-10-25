# pdf2md

Convert one PDF into Markdown with local engines.
Version 0.1.0 is the initial release for academic papers and books.
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
No PDF content leaves the computer.

## Convert

```sh
uv run --locked pdf2md paper.pdf --report
uv run --locked pdf2md book.pdf --quality best
uv run --locked pdf2md scan.pdf --source scan -o output/book.md
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
| `--report`, `--no-report` | Enable or disable the JSON report | Off |
| `--help` | Show command help | Off |

The JSON report uses schema version 1.
It records the input hash, settings, engine versions, timing, warnings, and page provenance.
Each page record includes its classification, engine, OCR use, VLM use, and extraction attempts.

The command prints progress and warnings to standard error.
It prints the output path to standard output after success.

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
A hidden work directory stores completed chunks beside the output.
Repeat the same command after interruption to resume.
Changes to the input, settings, models, or engine versions invalidate previous checkpoints.
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
Version 0.1.0 does not infer missing chapter levels or repair equations.

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

Include docstring examples with this command:

```sh
uv run --locked pytest --doctest-modules src tests
```

Run the separate model smoke test after the required weights exist in the cache:

```sh
HF_HUB_OFFLINE=1 uv run --locked pytest -m models
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
