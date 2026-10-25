"""Pure decisions and conservative Markdown checks."""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from pathlib import PurePosixPath

from pdf2md.models import ConversionError, PageInfo, PageResult, Settings


def text_warnings(text: str) -> list[str]:
    """Check text without guessing its intended content.

    >>> text_warnings("Readable text.")
    []
    >>> text_warnings("bad \\ufffd\\ufffd text")
    ['Text contains replacement, control, or private-use characters.']
    """
    chars = [c for c in text if not c.isspace()]
    bad = sum(c == "\ufffd" or unicodedata.category(c) in {"Cc", "Co"} for c in chars)
    warnings = []
    if chars and bad / len(chars) > 0.01:
        warnings.append(
            "Text contains replacement, control, or private-use characters."
        )
    if re.search(r"[<(]cid:\s*\d+", text, re.IGNORECASE):
        warnings.append("Text contains unresolved glyph identifiers.")
    return warnings


def classify_source(text: str, image_coverage: float, override: str) -> str:
    """Distinguish an OCR layer from ordinary digital text.

    >>> classify_source("OCR layer", 0.95, "auto")
    'ocred'
    >>> classify_source("", 1.0, "auto")
    'scan'
    """
    if override != "auto":
        return override
    if not text.strip():
        return "scan"
    return "ocred" if image_coverage >= 0.65 else "digital"


def repeated_margins(pages: list[set[str]]) -> set[str]:
    """Require repetition across at least three pages.

    >>> repeated_margins([{"Book"}, {"Book"}, {"Book"}])
    {'Book'}
    """
    counts = Counter(text for page in pages for text in page)
    return {text for text, count in counts.items() if count >= max(3, len(pages) * 0.6)}


def strip_margins(markdown: str, candidates: set[str], repeated: set[str]) -> str:
    """Remove confirmed margin lines only at page boundaries.

    >>> strip_margins("Book\\n\\nBody.\\n\\n42", {"Book", "42"}, {"Book"})
    '\\nBody.\\n'
    """
    lines = markdown.splitlines()
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    edges = set(nonempty[:3] + nonempty[-3:])
    kept = []
    for index, line in enumerate(lines):
        text = re.sub(r"^#{1,6}\s+", "", line.strip()).strip("*")
        number = re.fullmatch(r"(?:\d+|[ivxlcdm]+)", text, re.IGNORECASE)
        if index in edges and text in candidates and (text in repeated or number):
            continue
        kept.append(line)
    return "\n".join(kept)


def choose_engine(page: PageInfo, settings: Settings) -> str:
    """Choose one engine without importing model runtimes.

    >>> choose_engine(PageInfo(1, "digital", "Text"), Settings())
    'pdf-inspector'
    """
    if settings.engine != "auto":
        return settings.engine
    if settings.quality == "best":
        return "vlm"
    if page.needs_ocr or page.complex_layout:
        return "docling"
    return "pdf-inspector"


def validate_settings(settings: Settings) -> None:
    """Reject a request that contradicts fast mode.

    >>> validate_settings(Settings(quality="fast"))
    """
    if settings.quality == "fast" and settings.engine == "vlm":
        raise ConversionError("--engine vlm conflicts with --quality fast.")


def validate_text(markdown: str, page: PageInfo) -> None:
    """Reject unusable transcription while allowing confirmed blank pages.

    >>> validate_text("", PageInfo(1, "scan", "", blank=True))
    """
    if not markdown.strip() and not page.blank:
        raise ConversionError(f"Page {page.number}: extraction returned no content.")
    warnings = text_warnings(markdown)
    if warnings:
        raise ConversionError(f"Page {page.number}: {' '.join(warnings)}")


def markdown_warnings(markdown: str) -> list[str]:
    """Flag syntax without editing a transcription.

    >>> markdown_warnings("```python\\nx = 1\\n")
    ['Markdown has an unclosed code fence.']
    """
    warnings = []
    fence: str | None = None
    for line in markdown.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if not match:
            continue
        marker, tail = match.groups()
        if fence is None:
            fence = marker
        elif marker[0] == fence[0] and len(marker) >= len(fence) and not tail.strip():
            fence = None
    if fence:
        warnings.append("Markdown has an unclosed code fence.")
    if "<!-- image -->" in markdown:
        warnings.append("An image has no extracted pixels.")
    return warnings


def fidelity_warnings(native: str, markdown: str) -> list[str]:
    """Compare reliable native text without replacing the chosen transcription.

    >>> fidelity_warnings("Short text.", "Short text.")
    []
    """
    source = re.findall(r"\w+", native.casefold())
    extracted = re.findall(r"\w+", markdown.casefold())
    if len(source) < 20:
        return []
    common = sum((Counter(source) & Counter(extracted)).values())
    warnings = []
    if common / len(source) < 0.8:
        warnings.append("Extraction differs from native text. Verify missing content.")
    unique = {word for word, count in Counter(source).items() if count == 1}
    positions = {word: index for index, word in enumerate(source) if word in unique}
    anchors = [positions[word] for word in extracted if word in positions]
    if len(anchors) >= 10:
        inversions = sum(a > b for a, b in zip(anchors, anchors[1:], strict=False))
        if inversions / (len(anchors) - 1) > 0.15:
            warnings.append(
                "Reading order differs from native text. Verify the source."
            )
    return warnings


def safe_asset(name: str) -> bool:
    """Keep checkpoint assets inside their asset directory.

    >>> safe_asset("figure-p1-1.png")
    True
    >>> safe_asset("../outside.png")
    False
    """
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and len(path.parts) == 1
        and name not in {".", ".."}
        and "\\" not in name
    )


def validate_pages(pages: Iterable[PageResult], count: int) -> None:
    """Require every physical page exactly once and in order.

    >>> validate_pages([], 0)
    """
    numbers = [page.report.page for page in pages]
    if numbers != list(range(1, count + 1)):
        raise ConversionError(
            "Page coverage is incomplete, duplicated, or out of order."
        )


def boundary_warnings(previous: str, current: str) -> list[str]:
    """Keep ambiguous page boundaries visible in the report.

    >>> boundary_warnings("First paragraph.", "Second paragraph.")
    []
    """
    if not previous.strip() or not current.strip():
        return []
    last = previous.rstrip().splitlines()[-1]
    first = current.lstrip().splitlines()[0]
    if last.startswith("|") and first.startswith("|"):
        return ["Possible table continuation at the page boundary. Verify the source."]
    if last[-1:] not in ".!?。！？:|>" and first[:1].islower():
        return [
            "Possible paragraph continuation at the page boundary. Verify the source."
        ]
    return []
