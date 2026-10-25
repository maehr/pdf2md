from pathlib import Path

import pytest

from pdf2md.core import (
    boundary_warnings,
    choose_engine,
    classify_source,
    fidelity_warnings,
    markdown_warnings,
    repeated_margins,
    safe_asset,
    strip_margins,
    text_warnings,
    validate_pages,
    validate_settings,
    validate_text,
)
from pdf2md.models import (
    ConversionError,
    PageInfo,
    PageReport,
    PageResult,
    Paths,
    Settings,
)


@pytest.mark.parametrize(
    ("text", "coverage", "override", "expected"),
    [
        ("text", 0, "auto", "digital"),
        ("text", 0.9, "auto", "ocred"),
        ("", 0.9, "auto", "scan"),
        ("", 0, "digital", "digital"),
    ],
)
def test_classification(
    text: str, coverage: float, override: str, expected: str
) -> None:
    assert classify_source(text, coverage, override) == expected


@pytest.mark.parametrize(
    ("settings", "ocr", "layout", "expected"),
    [
        (Settings(), False, False, "pdf-inspector"),
        (Settings(quality="best"), False, False, "vlm"),
        (Settings(quality="fast"), True, False, "docling"),
        (Settings(quality="fast"), False, True, "docling"),
        (Settings(engine="pdf-inspector"), True, True, "pdf-inspector"),
    ],
)
def test_routing(settings: Settings, ocr: bool, layout: bool, expected: str) -> None:
    info = PageInfo(1, "digital", "text", needs_ocr=ocr, complex_layout=layout)
    assert choose_engine(info, settings) == expected


def test_quality_conflict() -> None:
    validate_settings(Settings(quality="fast"))
    with pytest.raises(ConversionError, match="conflicts"):
        validate_settings(Settings(quality="fast", engine="vlm"))


def test_text_quality() -> None:
    assert not text_warnings("Valid\ttext\nαβ 中文")
    assert text_warnings("\ufffd\ue000\x00")
    assert text_warnings("(cid:123)")
    assert not text_warnings("")
    validate_text("", PageInfo(1, "scan", "", blank=True))
    validate_text("text", PageInfo(1, "digital", "text"))
    with pytest.raises(ConversionError, match="no content"):
        validate_text("", PageInfo(1, "scan", ""))
    with pytest.raises(ConversionError, match="glyph"):
        validate_text("<cid:123>", PageInfo(1, "digital", ""))


def test_markdown_checks() -> None:
    assert markdown_warnings("```py\nx\n````") == []
    assert markdown_warnings("~~~py\nx\n~~~") == []
    assert markdown_warnings("```\n~~~\n```") == []
    assert markdown_warnings("```\nx")
    assert markdown_warnings("<!-- image -->")
    assert not markdown_warnings("plain text")


def test_fidelity_checks() -> None:
    words = [f"token{n}" for n in range(30)]
    source = " ".join(words)
    assert not fidelity_warnings(source, source)
    assert not fidelity_warnings("short", "")
    assert "missing" in fidelity_warnings(source, "")[0]
    assert "order" in fidelity_warnings(source, " ".join(reversed(words)))[0]
    assert not fidelity_warnings("word " * 30, "word " * 30)


@pytest.mark.parametrize("name", ["../bad", "/bad", "a/b", "..", ".", "", "a\\b"])
def test_unsafe_assets(name: str) -> None:
    assert not safe_asset(name)


def test_assets_and_paths() -> None:
    assert safe_asset("figure.png")
    paths = Paths(Path("input.pdf"), Path("output/book.md"))
    assert paths.assets == Path("output/book.assets")
    assert paths.report == Path("output/book.report.json")
    assert paths.work == Path("output/.book.md.pdf2md-work")


def test_page_coverage() -> None:
    page = PageResult(
        markdown="text",
        report=PageReport(page=1, source="digital", engine="vlm", ocr=False, vlm=True),
    )
    validate_pages([page], 1)
    with pytest.raises(ConversionError, match="coverage"):
        validate_pages([page, page], 2)


def test_boundaries() -> None:
    assert not boundary_warnings("", "text")
    assert not boundary_warnings("text", "")
    assert not boundary_warnings("Complete.", "New paragraph.")
    assert boundary_warnings("| a |", "| b |")
    assert boundary_warnings("unfinished", "continuation")


def test_conservative_margins() -> None:
    assert repeated_margins([{"Book"}, {"Book"}]) == set()
    assert repeated_margins([{"Book"}, {"Book"}, {"Book"}]) == {"Book"}
    markdown = "# Book\n\nBody.\n\nReferences: Example (2026).\n\n42"
    candidates = {"Book", "42", "References: Example (2026)."}
    result = strip_margins(markdown, candidates, {"Book"})
    assert "Book" not in result
    assert "42" not in result
    assert "References: Example (2026)." in result
    assert "Chapter" in strip_margins("Chapter\n\nBody.", set(), {"Chapter"})
    assert "body Book" in strip_margins("body Book", {"Book"}, {"Book"})
