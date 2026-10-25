from pathlib import Path
from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest
from conftest import make_spread
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf2md.engines import Docling, Inspector
from pdf2md.models import PageInfo, Paths, Quality, Settings
from pdf2md.pipeline import check_assets


@pytest.mark.integration
def test_scan_and_ocr_layer_classification(tmp_path: Path) -> None:
    image = Image.new("RGB", (600, 800), "gray")
    path = tmp_path / "mixed.pdf"
    document = canvas.Canvas(str(path), pagesize=(600, 800))
    document.drawImage(ImageReader(image), 0, 0, 600, 800)
    document.showPage()
    document.drawImage(ImageReader(image), 0, 0, 600, 800)
    text = document.beginText(20, 700)
    text.setTextRenderMode(3)
    text.textLine("Existing invisible OCR text with a full page image.")
    document.drawText(text)
    document.showPage()
    document.showPage()
    document.save()
    inspector = Inspector(path)
    try:
        infos = inspector.inspect([1, 2, 3], Settings())
        assert [p.source for p in infos[:2]] == ["scan", "ocred"]
        assert infos[0].needs_ocr
        assert infos[2].blank
        assert not infos[2].needs_ocr
    finally:
        inspector.close()


@pytest.mark.integration
def test_native_image_assets(pdf: Path, tmp_path: Path) -> None:
    document = canvas.Canvas(str(tmp_path / "figure.pdf"))
    for index in range(5):
        document.drawString(40, 750 - index * 20, "An academic figure and its caption.")
    document.drawImage(ImageReader(Image.new("RGB", (30, 30), "blue")), 40, 200, 50, 50)
    document.save()
    inspector = Inspector(tmp_path / "figure.pdf")
    assets = tmp_path / "assets"
    assets.mkdir()
    try:
        infos = inspector.inspect([1], Settings())
        result = inspector.extract(infos, assets, "figure.assets/")[1]
        assert result.assets == ["native-p1-1.png"]
        check_assets(result, assets, "figure.assets/")
    finally:
        inspector.close()


def test_inspection_failure_still_allows_ocr(
    pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pdf_inspector

    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("unsupported text encoding")

    monkeypatch.setattr(pdf_inspector, "extract_pages_markdown", fail)
    inspector = Inspector(pdf)
    try:
        page = inspector.inspect([1], Settings())[0]
        assert page.native_text
        assert page.needs_ocr
        assert page.warnings
    finally:
        inspector.close()


def test_docling_export_without_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docling.datamodel.base_models import ConversionStatus
    from docling_core.types.doc import (
        BoundingBox,
        ContentLayer,
        CoordOrigin,
        DocItemLabel,
        DoclingDocument,
        ImageRef,
        ProvenanceItem,
        Size,
        TableCell,
        TableData,
    )

    document = DoclingDocument(name="test")
    document.add_page(9, Size(width=600, height=800))
    provenance = ProvenanceItem(
        page_no=9,
        bbox=BoundingBox(l=0, t=100, r=200, b=0, coord_origin=CoordOrigin.BOTTOMLEFT),
        charspan=(0, 10),
    )
    document.add_text(
        label=DocItemLabel.PAGE_HEADER,
        text="Running header",
        prov=provenance,
        content_layer=ContentLayer.FURNITURE,
    )
    document.add_text(label=DocItemLabel.TEXT, text="Body text.", prov=provenance)
    document.add_picture(
        image=ImageRef.from_pil(Image.new("RGB", (20, 20), "red"), dpi=72),
        prov=provenance,
    )
    document.add_table(
        data=TableData(
            num_rows=1,
            num_cols=2,
            table_cells=[
                TableCell(
                    text="Merged cell",
                    row_span=1,
                    col_span=2,
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=2,
                )
            ],
        ),
        prov=provenance,
    )
    fake = SimpleNamespace(
        convert=lambda *a, **kw: SimpleNamespace(
            status=ConversionStatus.SUCCESS, document=document
        )
    )
    monkeypatch.setattr(Docling, "converter", lambda *a: fake)
    adapter = Docling(tmp_path / "source.pdf")
    assets = tmp_path / "assets"
    assets.mkdir()
    page = adapter.extract(
        [PageInfo(9, "digital", "Body text.")], "vlm", assets, "book.assets/"
    )[9]
    assert "Body text." in page.markdown
    assert "Running header" in page.markdown
    assert "<table" in page.markdown
    assert 'colspan="2"' in page.markdown
    assert page.assets
    check_assets(page, assets, "book.assets/")
    assert page.report.provenance[0].page == 9
    assert page.report.provenance[0].bbox == [0, 700, 200, 800]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_split_displayed_halves(tmp_path: Path, rotation: int) -> None:
    source = make_spread(tmp_path / "spread.pdf", 3)
    with pdfium.PdfDocument(source) as document:
        page = document[2]
        page.set_rotation(rotation)
        page.close()
        document.save(tmp_path / "rotated.pdf")
    inspector = Inspector(tmp_path / "rotated.pdf")
    try:
        parts = inspector.split_pages([3], tmp_path / "halves.pdf")
        assert [(part.page, part.half) for part in parts] == [(3, "left"), (3, "right")]
        with pdfium.PdfDocument(tmp_path / "halves.pdf") as halves:
            source_page = inspector.pdf[2]
            width, height = source_page.get_size()
            bitmap = source_page.render(scale=2)
            image = bitmap.to_pil().copy()
            bitmap.close()
            source_page.close()
            assert parts[0].crop == (0, 0, width / 2, height)
            assert parts[1].crop == (width / 2, 0, width, height)
            for index in range(2):
                page = halves[index]
                assert page.get_size() == (width / 2, height)
                bitmap = page.render(scale=2)
                expected = image.crop(
                    (index * width, 0, (index + 1) * width, height * 2)
                )
                assert (
                    bitmap.to_pil().convert("RGB").tobytes()
                    == expected.convert("RGB").tobytes()
                )
                bitmap.close()
                page.close()
    finally:
        inspector.close()


@pytest.mark.models
def test_live_vlm(pdf: Path, tmp_path: Path) -> None:
    from pdf2md.models import Paths
    from pdf2md.pipeline import convert

    report = convert(
        Paths(pdf, tmp_path / "vlm.md"), Settings(quality="best", engine="vlm")
    )
    assert report.pages[0].engine == "vlm"
    assert report.model_revision
    assert "research" in (tmp_path / "vlm.md").read_text().lower()


@pytest.mark.models
@pytest.mark.parametrize("quality", ["fast", "best"])
def test_live_split_scan(tmp_path: Path, quality: Quality) -> None:
    from pdf2md.pipeline import convert

    vector = tmp_path / "vector.pdf"
    document = canvas.Canvas(str(vector), pagesize=(800, 400))
    for x, side in ((0, "Left"), (400, "Right")):
        document.setFont("Helvetica-Bold", 18)
        document.drawString(x + 30, 350, f"{side} research section")
        document.setFont("Helvetica", 11)
        for row, text in enumerate(
            [
                "Local conversion preserves the original text.",
                "Readers check each sentence against the source.",
                "Each half contains a separate research section.",
                "Figure 1. A measured signal.",
            ]
        ):
            document.drawString(x + 30, 310 - row * 22, text)
        document.line(x + 30, 60, x + 300, 60)
        document.line(x + 30, 60, x + 30, 200)
        document.lines(
            [
                (x + 30, 60, x + 100, 140),
                (x + 100, 140, x + 170, 100),
                (x + 170, 100, x + 300, 200),
            ]
        )
    document.save()
    with pdfium.PdfDocument(vector) as source:
        page = source[0]
        bitmap = page.render(scale=2)
        image = bitmap.to_pil().copy()
        bitmap.close()
        page.close()
    scan = tmp_path / "scan.pdf"
    document = canvas.Canvas(str(scan), pagesize=(800, 400))
    document.drawImage(ImageReader(image), 0, 0, 800, 400)
    document.save()
    paths = Paths(scan, tmp_path / "split.md")
    report = convert(
        paths, Settings(split="always", quality=quality), write_report=True
    )
    assert [(page.page, page.half) for page in report.pages] == [
        (1, "left"),
        (1, "right"),
    ]
    assert [page.engine for page in report.pages] == [
        "vlm" if quality == "best" else "docling"
    ] * 2
    markdown = paths.output.read_text().lower()
    assert markdown.index("left research") < markdown.index("right research")
    assert "original text" in markdown
    assert markdown.count("![image](") == 2
    assert all(page.provenance for page in report.pages)
    for page in report.pages:
        assert all(provenance.page == 1 for provenance in page.provenance)
        assert all(
            provenance.bbox[0] >= page.crop[0] - 1
            and provenance.bbox[2] <= page.crop[2] + 1
            for provenance in page.provenance
        )
