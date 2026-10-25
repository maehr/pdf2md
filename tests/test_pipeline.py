import json
from pathlib import Path

import pytest
from conftest import make_pdf
from typer.testing import CliRunner

from pdf2md import pipeline
from pdf2md.cli import app
from pdf2md.engines import Docling, Inspector
from pdf2md.models import (
    ConversionError,
    PageInfo,
    PageReport,
    PageResult,
    Paths,
    Settings,
)


@pytest.mark.integration
def test_real_native_conversion(pdf: Path, tmp_path: Path) -> None:
    output = tmp_path / "custom folder" / "custom name.md"
    result = pipeline.convert(Paths(pdf, output), Settings(), write_report=True)
    assert "Unique page marker 0001" in output.read_text()
    assert result.pages[0].engine == "pdf-inspector"
    assert result.pages[0].page == 1
    assert json.loads(output.with_suffix(".report.json").read_text())["page_count"] == 1
    assert not Paths(pdf, output).work.exists()


def test_resume_completed_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", 17)
    paths = Paths(pdf, tmp_path / "book.md")
    original = Inspector.inspect

    def interrupt(
        self: Inspector, numbers: list[int], settings: Settings
    ) -> list[PageInfo]:
        if numbers[0] == 9:
            raise KeyboardInterrupt
        return original(self, numbers, settings)

    monkeypatch.setattr(Inspector, "inspect", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pipeline.convert(paths, Settings(quality="fast"))
    assert not paths.output.exists()
    assert len(list(paths.work.glob("*/chunk-*.json"))) == 1
    monkeypatch.setattr(Inspector, "inspect", original)
    result = pipeline.convert(paths, Settings(quality="fast"), write_report=True)
    assert result.resumed_pages == 8
    assert [p.page for p in result.pages] == list(range(1, 18))
    text = paths.output.read_text()
    for number in range(1, 18):
        assert text.count(f"Unique page marker {number:04d}") == 1
    assert not paths.work.exists()


def test_changed_settings_invalidate_cache(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(pdf, tmp_path / "output.md")

    def stop(page: int, count: int, label: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        pipeline.convert(paths, Settings(quality="fast"), progress=stop)
    result = pipeline.convert(paths, Settings(quality="fast", source="digital"))
    assert result.resumed_pages == 0


def test_corrupt_checkpoint_retries(pdf: Path, tmp_path: Path) -> None:
    paths = Paths(pdf, tmp_path / "output.md")

    def stop(page: int, count: int, label: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        pipeline.convert(paths, Settings(quality="fast"), progress=stop)
    checkpoint = next(paths.work.glob("*/chunk-*.json"))
    checkpoint.write_text("broken JSON")
    assert pipeline.convert(paths, Settings(quality="fast")).resumed_pages == 0


def test_vlm_best_and_fallback(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    monkeypatch.setattr(Docling, "prepare_vlm", lambda self: None)

    def extract(
        self: Docling,
        infos: list[PageInfo],
        engine: str,
        assets: Path,
        prefix: str,
    ) -> dict[int, PageResult]:
        called.append(engine)
        if engine == "vlm":
            raise RuntimeError("model unavailable")
        return {
            info.number: PageResult(
                markdown=info.native_text,
                report=PageReport(
                    page=info.number,
                    source=info.source,
                    engine=engine,
                    ocr=info.needs_ocr,
                    vlm=False,
                ),
            )
            for info in infos
        }

    monkeypatch.setattr(Docling, "extract", extract)
    result = pipeline.convert(
        Paths(pdf, tmp_path / "output.md"), Settings(quality="best")
    )
    assert called == ["vlm", "docling"]
    assert [a.engine for a in result.pages[0].attempts] == ["vlm", "docling"]
    assert result.pages[0].warnings
    called.clear()
    with pytest.raises(ConversionError, match="model unavailable"):
        pipeline.convert(
            Paths(pdf, tmp_path / "forced.md"), Settings(quality="best", engine="vlm")
        )
    assert called == ["vlm"]


def test_failure_preserves_previous_output(pdf: Path, tmp_path: Path) -> None:
    output = tmp_path / "output.md"
    output.write_text("Previous output")
    with pytest.raises(ConversionError, match="requires OCR"):
        pipeline.convert(
            Paths(pdf, output), Settings(source="scan", engine="pdf-inspector")
        )
    assert output.read_text() == "Previous output"


def test_publish_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = Paths(tmp_path / "input.pdf", tmp_path / "output.md")
    paths.output.write_text("old text")
    paths.assets.mkdir()
    (paths.assets / "old.png").write_bytes(b"old image")
    stage = tmp_path / "stage"
    stage.mkdir()
    markdown = stage / "output.md"
    markdown.write_text("new text")
    assets = stage / "assets"
    assets.mkdir()
    (assets / "new.png").write_bytes(b"new image")
    replace = Path.replace

    def fail(self: Path, target: Path) -> Path:
        if self == markdown:
            raise OSError("disk failure")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        pipeline.publish(paths, markdown, None, assets)
    assert paths.output.read_text() == "old text"
    assert (paths.assets / "old.png").read_bytes() == b"old image"


@pytest.mark.parametrize("content", [b"not a PDF", b"%PDF-1.7\nbroken"])
def test_corrupt_input(tmp_path: Path, content: bytes) -> None:
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(content)
    result = CliRunner().invoke(app, [str(pdf)])
    assert result.exit_code == 1
    assert "Cannot open PDF" in result.output


def test_cli_errors(pdf: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, [str(pdf), "--source", "bad"]).exit_code == 2
    conflict = runner.invoke(app, [str(pdf), "--engine", "vlm", "--quality", "fast"])
    assert conflict.exit_code == 1
    assert "conflicts" in conflict.output
    missing = runner.invoke(app, [str(tmp_path / "missing.pdf")])
    assert missing.exit_code == 1
    assert "does not exist" in missing.output
    same = runner.invoke(app, [str(pdf), "-o", str(pdf)])
    assert "must differ" in same.output
    assert same.exit_code == 1


def test_asset_links(tmp_path: Path) -> None:
    page = PageResult(
        markdown="![figure](book.assets/image.png)",
        assets=["image.png"],
        report=PageReport(page=1, source="digital", engine="vlm", ocr=False, vlm=True),
    )
    with pytest.raises(ConversionError, match="missing"):
        pipeline.check_assets(page, tmp_path, "book.assets/")
    (tmp_path / "image.png").write_bytes(b"pixels")
    pipeline.check_assets(page, tmp_path, "book.assets/")
    page.markdown = "![figure](missing.png)"
    with pytest.raises(ConversionError, match="broken"):
        pipeline.check_assets(page, tmp_path, "book.assets/")
    page.assets = ["../image.png"]
    with pytest.raises(ConversionError, match="unsafe"):
        pipeline.check_assets(page, tmp_path, "book.assets/")


def test_unrelated_work_directory(pdf: Path, tmp_path: Path) -> None:
    paths = Paths(pdf, tmp_path / "output.md")
    paths.work.mkdir()
    unrelated = paths.work / "keep.txt"
    unrelated.write_text("user file")
    with pytest.raises(ConversionError, match="unrelated"):
        pipeline.convert(paths, Settings(quality="fast"))
    assert unrelated.read_text() == "user file"


def test_encrypted_input(tmp_path: Path) -> None:
    from reportlab.lib.pdfencrypt import StandardEncryption
    from reportlab.pdfgen import canvas

    path = tmp_path / "locked.pdf"
    pdf = canvas.Canvas(str(path), encrypt=StandardEncryption("secret"))
    pdf.drawString(50, 700, "Protected text")
    pdf.save()
    result = CliRunner().invoke(app, [str(path), "--quality", "fast"])
    assert result.exit_code == 1
    assert "Cannot open PDF" in result.output


def test_blank_page_has_no_inference(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    path = tmp_path / "blank.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.showPage()
    pdf.save()
    result = pipeline.convert(
        Paths(path, tmp_path / "blank.md"), Settings(quality="fast")
    )
    assert result.pages[0].blank
    assert result.pages[0].engine == "none"
    assert not result.pages[0].ocr
    assert not (tmp_path / "blank.assets").exists()
