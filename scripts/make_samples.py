"""Generate small layout samples and a 300-page memory test."""

import argparse
import textwrap
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

PARAGRAPH = (
    "Local document conversion preserves source text and document structure. "
    "A reader checks extracted text against the original page. "
    "Tables, equations, citations, and captions require separate quality checks."
)


def paper(path: Path, columns: bool = False) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(612, 792))
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, 740, "Local Document Conversion")
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(50, 700, "1. Method")
    pdf.setFont("Helvetica", 11)
    for column in range(2 if columns else 1):
        for index, line in enumerate(textwrap.wrap(PARAGRAPH, 37 if columns else 78)):
            pdf.drawString(50 + column * 270, 675 - index * 17, line)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(50, 430, "2. Results")
    pdf.setFont("Helvetica", 11)
    for row, values in enumerate([("Method", "Pages"), ("Native", "12"), ("OCR", "8")]):
        y = 400 - row * 25
        pdf.drawString(55, y, values[0])
        pdf.drawString(225, y, values[1])
        pdf.line(50, y - 8, 350, y - 8)
    for x in (50, 210, 350):
        pdf.line(x, 342, x, 420)
    pdf.drawString(50, 290, "Equation: E = m c^2")
    pdf.drawString(50, 250, "Figure 1. A measured signal.")
    pdf.line(55, 150, 300, 150)
    pdf.line(55, 150, 55, 230)
    pdf.lines([(55, 150, 100, 190), (100, 190, 180, 170), (180, 170, 300, 230)])
    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, 75, "1. This footnote belongs to the method.")
    pdf.drawString(50, 50, "References: Example, A. (2026). Local conversion.")
    pdf.save()


def scan(source: Path, destination: Path, ocr_layer: bool = False) -> None:
    with pdfium.PdfDocument(source) as original:
        page = original[0]
        try:
            with closing(page.render(scale=2)) as bitmap:
                image = bitmap.to_pil().copy()
        finally:
            page.close()
    pdf = canvas.Canvas(str(destination), pagesize=(612, 792))
    pdf.drawImage(ImageReader(image), 0, 0, 612, 792)
    if ocr_layer:
        text = pdf.beginText(50, 600)
        text.setTextRenderMode(3)
        text.textLine("(cid:123) (cid:456) defective OCR text")
        pdf.drawText(text)
    pdf.save()


def book(path: Path, pages: int) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(612, 792))
    for number in range(1, pages + 1):
        pdf.setFont("Helvetica", 9)
        pdf.drawString(50, 765, "A Synthetic Book")
        pdf.drawString(300, 30, str(number))
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(50, 710, f"Chapter {(number - 1) // 10 + 1}")
        pdf.setFont("Helvetica", 11)
        for index, line in enumerate(textwrap.wrap(PARAGRAPH * 8, 78)):
            pdf.drawString(50, 670 - index * 17, line)
        pdf.drawString(50, 100, f"Unique page marker {number:04d}.")
        pdf.showPage()
    pdf.save()


def generate(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    paper(output / "clean.pdf")
    paper(output / "columns.pdf", columns=True)
    scan(output / "clean.pdf", output / "scan.pdf")
    scan(output / "clean.pdf", output / "ocred.pdf", ocr_layer=True)
    book(output / "book.pdf", 300)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evaluation/samples"))
    generate(parser.parse_args().output)
