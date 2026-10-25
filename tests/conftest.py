"""Small PDFs for local, repeatable tests."""

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

TEXT = (
    "This research paper describes a local document conversion method. "
    "The method preserves the original text without summary or invention. "
    "Readers can check each extracted sentence against the source document."
)


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


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "paper.pdf")
