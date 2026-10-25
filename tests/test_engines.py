from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf2md.engines import Docling, Inspector
from pdf2md.models import PageInfo, Settings
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
        page_no=9, bbox=BoundingBox(l=0, t=100, r=200, b=0), charspan=(0, 10)
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
