# MVP evaluation

Recorded on 2026-10-25 with an Apple M5 Pro and 48 GB of memory.
The environment used macOS 26.6.2 and Python 3.12.15.
The lockfile records the package versions.
The MLX model uses revision `e9939db25d2f296c8678d0491c4609a8c596c50a`.

## Method

Each engine ran in a separate process.
The final layout runs used cached weights with `HF_HUB_OFFLINE=1`.
Wall time includes process startup and output publication.
Peak memory comes from `wait4` for that process.
These results show single runs, without statistical confidence intervals.

The public paper was [DocLayNet, arXiv:2206.01062](https://arxiv.org/abs/2206.01062).
The sample generator supplied clean, two-column, scanned, and defective OCR pages.
The sample generator also supplied a 300-page book for memory checks.

## Measurements

| Input | Engine | Pages | Seconds | Peak MiB | Result |
| --- | --- | ---: | ---: | ---: | --- |
| DocLayNet | pdf-inspector | 9 | 0.282 | 97.3 | Requested OCR on page 1 |
| DocLayNet | Docling | 9 | 13.155 | 1764.0 | Complete page coverage |
| DocLayNet | MLX VLM | 9 | 42.521 | 2566.5 | Complete page coverage |
| Generated book | pdf-inspector | 300 | 2.350 | 72.3 | Complete page coverage |

The four generated single-page samples produced these ranges:

| Engine | Seconds | Peak MiB | Result |
| --- | --- | --- | --- |
| pdf-inspector | 0.135 to 0.140 | 57.0 to 57.3 | Clean and two-column pages passed |
| Docling | 3.637 to 4.568 | 1091.7 to 1180.4 | All four samples passed |
| MLX VLM | 4.367 to 4.508 | 1693.1 to 1698.0 | All four samples passed |

Explicit pdf-inspector conversion rejected the scanned and defective OCR samples.
Automatic routing uses Docling for these pages in fast mode.
These runs used the previous best-mode default.
The current default is fast mode.
These measurements do not show a general MLX speed advantage.

## Fidelity checks

Initial visual review covered the generated layouts and the public paper's first page.
The checks confirmed selected text, table cells, figure links, captions, and the generated reference line.
The later review below found missing footnotes and damaged tables.
The footer filter initially removed the reference line.
The revised filter preserved that line in subsequent Docling and MLX runs.
The 300-page output contained every unique page marker once.
The filter removed all repeated book headers from that output.

No quantitative fidelity score exists for this corpus.
The review did not verify every word or equation in the public paper.
Full scanned books and formula-heavy papers still need review with representative source PDFs.

## Informal quality comparison

This review used existing engine outputs and new automatic fast conversions.
The inputs were four generated pages and the nine-page DocLayNet paper.
Visual checks covered the generated layouts and paper pages 1, 7, and 8.
The review used selected passages, without reference transcriptions or numeric quality scores.
This comparison does not establish a general quality ranking.

| Mode or engine | Observed strengths | Observed failures |
| --- | --- | --- |
| Automatic fast | Preserved sample prose, column order, simple tables, captions, and figure crops. | Dropped the footnote in all four generated samples. |
| Automatic best, MLX VLM | Preserved the sample footnote and separated the paper's authors with their affiliations. | Shifted numeric values between rows in the paper's Table 5. |
| Explicit Docling | Preserved the simple tables and Table 4's grouped header. | Dropped sample footnotes and lost some Table 5 row-group labels. |
| Explicit pdf-inspector | Preserved clean text, simple table values, and the sample footnote. | Interleaved two-column text, omitted the vector figure, and treated the equation as a heading. |

Automatic fast selected Docling for every page in these five inputs.
Thus, its results here do not test automatic native extraction.
Explicit pdf-inspector rejected the scan, defective OCR sample, and paper, as recorded above.

The VLM preserved the paper's first author email, which Docling omitted.
It also avoided the figure's internal text fragments that Docling inserted into the main text.
Both Docling and the VLM gave the generated title and section headings the same level.
The generated equation was plain text, so this check does not establish mathematical recognition quality.

Table 5 exposed the most serious VLM error.
The source gives DocBank's Figure row the values 77, 71, and 31.
The VLM assigned that row 93, 34, and 30, from PubLayNet's total row.
It shifted later rows too, while producing plausible HTML.
Docling kept those row values aligned but failed to extend some group labels across all corresponding rows.
In Table 4, the VLM also damaged the grouped header that Docling preserved.

For these inputs, fast mode supplied useful body text and simple tables.
Best mode improved footnotes and author blocks, but its complex tables still required source checks.
Neither mode supplied dependable output for every checked structure.
The findings support checking footnotes in fast output and numeric tables in best output first.

The new fast outputs and reports remain under `evaluation/quality-spotcheck/`.
The reviewed engine outputs remain under `evaluation/final/`.

## Known limits

- Engine output can join words around printed line breaks.
- Engine output can assign the same heading level to a title and a section.
- Short final chunks can retain unconfirmed running headers.
- Ambiguous paragraph and table continuations remain separate.
- Native image extraction places images after page text.
- Synthetic scans do not cover historical fonts, poor scans, or handwriting.
- The checks cannot prove that an extraction matches its source.

## Verification

The deterministic suite passed 54 tests, including docstring examples.
The core module reached 100 percent statement coverage.
The separate live MLX test passed in offline mode.
Ruff, ty, and the wheel build passed.
Recovery tests confirmed reuse of eight completed pages after interruption.
Failure tests confirmed preservation of previous output.

Raw measurements and conversion logs remain under the ignored `evaluation/` directory.
