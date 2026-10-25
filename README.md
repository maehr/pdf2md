# pdf2md

Convert one PDF into Markdown with local engines.
The default fast mode uses native text and Apple Vision OCR through Docling.
Best mode uses GraniteDocling through MLX on Apple Silicon.

This MVP targets Apple Silicon macOS with Python 3.12.

## Install

```sh
uv sync --locked
```

The first run for an engine downloads its required models into the standard Hugging Face cache.
Later runs reuse cached weights.
No PDF content leaves the computer.

## Convert

```sh
uv run --locked pdf2md paper.pdf --report
uv run --locked pdf2md book.pdf --quality best
uv run --locked pdf2md scan.pdf --source scan -o output/book.md
```

The default output is `paper.md` beside `paper.pdf`.
Figures use `paper.assets/`.
The optional report uses `paper.report.json`.
Custom output paths determine the asset and report names.

| Option | Values | Default |
| --- | --- | --- |
| `--source` | `auto`, `digital`, `ocred`, `scan` | `auto` |
| `--quality` | `fast`, `best` | `fast` |
| `--engine` | `auto`, `pdf-inspector`, `docling`, `vlm` | `auto` |
| `--report` | Write a JSON report | Off |

Best mode uses MLX for every page.
Automatic routing retries failed VLM pages with standard Docling.
Fast mode avoids all VLM inference, including formula enrichment.
Explicit engine selection disables engine fallback.
The combination `--engine vlm --quality fast` is invalid.
Explicit VLM selection requires `--quality best`.
The source override changes classification.
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

Docling supplies document structure, tables, equations, captions, and figure crops.
The application preserves that structure without rewriting text.
It removes repeated margin text and page numbers.
It preserves unconfirmed footer text, including references near the page edge.
It preserves ambiguous content and reports warnings.
Native extraction also saves embedded images.

The checks detect missing pages, invalid text, and broken asset links.
They cannot prove that a transcription matches its source.
Inspect important quotations, formulas, reading order, and table continuations against the PDF.
Ambiguous boundaries between chunks remain separate.
The MVP does not infer missing chapter levels or repair equations.

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

See `EVALUATION.md` for measured results and known failures.
