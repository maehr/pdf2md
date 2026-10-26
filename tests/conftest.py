"""Small PDFs for local, repeatable tests."""

import re
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

PAGE_MARKER = re.compile(r"<!-- pdf-page: (\d+)(?:; half: (left|right))? -->")
PAGE_MARKER_PREFIX = "<!-- pdf-page:"

TEXT = (
    "This research paper describes a local document conversion method. "
    "The method preserves the original text without summary or invention. "
    "Readers can check each extracted sentence against the source document."
)


def page_markers(markdown: str) -> list[tuple[int, str | None]]:
    """Return physical page and half pairs from page markers, in order."""
    return [
        (int(number), half or None) for number, half in PAGE_MARKER.findall(markdown)
    ]


def page_numbers(markdown: str) -> list[int]:
    """Return physical page numbers from page markers, in order."""
    return [number for number, _ in page_markers(markdown)]


def make_pdf(path: Path, pages: int = 1) -> Path:
    document = canvas.Canvas(str(path), pagesize=(612, 792))
    for page in range(1, pages + 1):
        document.setFont("Helvetica-Bold", 18)
        document.drawString(72, 720, f"Research section {page}")
        document.setFont("Helvetica", 11)
        for index, line in enumerate(TEXT.split(". ")):
            document.drawString(72, 670 - index * 22, line)
        document.drawString(72, 570, f"Unique page marker {page:04d}.")
        document.showPage()
    document.save()
    return path


def make_spread(path: Path, pages: int = 1) -> Path:
    document = canvas.Canvas(str(path), pagesize=(400, 200))
    for number in range(1, pages + 1):
        document.setFillColorRGB(1, 0, 0)
        document.rect(0, 0, 200, 200, fill=1, stroke=0)
        document.setFillColorRGB(0, 0, 1)
        document.rect(200, 0, 200, 200, fill=1, stroke=0)
        document.setFillColorRGB(1, 1, 1)
        document.drawString(20, 150, f"Left page {number}")
        document.drawString(220, 150, f"Right page {number}")
        document.showPage()
    document.save()
    return path


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "paper.pdf")
