import hashlib
import json
from pathlib import Path

import pytest
from conftest import make_pdf, make_spread
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
    Provenance,
    Quality,
    Settings,
    Split,
)


@pytest.fixture
def fake_docling(monkeypatch: pytest.MonkeyPatch) -> list[list[int]]:
    calls: list[list[int]] = []
    monkeypatch.setattr(Docling, "prepare_vlm", lambda self: None)

    def extract(
        self: Docling,
        infos: list[PageInfo],
        engine: str,
        assets: Path,
        prefix: str,
    ) -> dict[int, PageResult]:
        calls.append([info.number for info in infos])
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()[:12]
        results = {}
        for info in infos:
            name = f"{digest}-{info.number}.png"
            (assets / name).write_bytes(b"pixels")
            results[info.number] = PageResult(
                markdown=f"Half {info.number}.\n\n![Figure]({prefix}{name})",
                assets=[name],
                report=PageReport(
                    page=info.number,
                    source=info.source,
                    engine=engine,
                    ocr=engine == "docling" and info.needs_ocr,
                    vlm=engine == "vlm",
                    provenance=[
                        Provenance(
                            page=info.number, label="picture", bbox=[10, 20, 30, 40]
                        )
                    ],
                ),
            )
        return results

    monkeypatch.setattr(Docling, "extract", extract)
    return calls


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


def test_noncontiguous_native_pages(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", 9)
    paths = Paths(pdf, tmp_path / "selected.md")
    progress: list[tuple[int, int, str]] = []
    report = pipeline.convert(
        paths,
        Settings(pages="9,3,1,7-9,3"),
        write_report=True,
        progress=lambda *args: progress.append(args),
    )
    assert report.page_count == 9
    assert report.selected_pages == [1, 3, 7, 8, 9]
    assert [page.page for page in report.pages] == [1, 3, 7, 8, 9]
    assert [page.provenance[0].page for page in report.pages] == [1, 3, 7, 8, 9]
    assert [step[:2] for step in progress] == [(n, 5) for n in range(1, 6)]
    markdown = paths.output.read_text()
    for number in range(1, 10):
        assert markdown.count(f"Unique page marker {number:04d}") == (
            number in report.selected_pages
        )


@pytest.mark.parametrize("quality", ["fast", "best"])
def test_docling_receives_separate_ranges_for_gaps(
    tmp_path: Path, fake_docling: list[list[int]], quality: Quality
) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", 9)
    report = pipeline.convert(
        Paths(pdf, tmp_path / "selected.md"),
        Settings(pages="9,3,1,7-9,3", quality=quality, source="scan"),
    )
    assert fake_docling == [[1], [3], [7, 8, 9]]
    assert [page.page for page in report.pages] == [1, 3, 7, 8, 9]


@pytest.mark.parametrize("selection", ["", "1,", "3-1", "0", "2", "1-999999999"])
def test_invalid_selection_precedes_inference(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    def fail(*args: object) -> None:
        pytest.fail("Invalid selections must fail before inspection or inference.")

    monkeypatch.setattr(Docling, "prepare_vlm", fail)
    monkeypatch.setattr(Inspector, "inspect", fail)
    paths = Paths(pdf, tmp_path / "selected.md")
    paths.output.write_text("Previous output")
    with pytest.raises(ConversionError, match="--pages"):
        pipeline.convert(paths, Settings(pages=selection, quality="best"))
    assert paths.output.read_text() == "Previous output"


@pytest.mark.parametrize("quality", ["fast", "best"])
def test_split_order_provenance_and_assets(
    tmp_path: Path, fake_docling: list[list[int]], quality: Quality
) -> None:
    pdf = make_spread(tmp_path / "spread.pdf", 4)
    paths = Paths(pdf, tmp_path / "chapter.md")
    progress: list[tuple[int, int, str]] = []
    report = pipeline.convert(
        paths,
        Settings(pages="4,2", split="always", quality=quality),
        write_report=True,
        progress=lambda *args: progress.append(args),
    )
    assert fake_docling == [[1, 2, 3, 4]]
    assert [(page.page, page.half) for page in report.pages] == [
        (2, "left"),
        (2, "right"),
        (4, "left"),
        (4, "right"),
    ]
    assert [page.crop for page in report.pages] == [
        [0, 0, 200, 200],
        [200, 0, 400, 200],
    ] * 2
    assert [page.provenance[0].page for page in report.pages] == [2, 2, 4, 4]
    assert [page.provenance[0].bbox for page in report.pages] == [
        [10, 20, 30, 40],
        [210, 20, 230, 40],
    ] * 2
    extraction_progress = [step for step in progress if step[0]]
    assert [step[:2] for step in extraction_progress] == [(n, 4) for n in range(1, 5)]
    assert "Page 2 left" in extraction_progress[0][2]
    assert "Page 4 right" in extraction_progress[-1][2]
    markdown = paths.output.read_text()
    assert [markdown.index(f"Half {n}.") for n in range(1, 5)] == sorted(
        markdown.index(f"Half {n}.") for n in range(1, 5)
    )
    assert len(list(paths.assets.iterdir())) == 4
    saved = json.loads(paths.report.read_text())
    assert saved["selected_pages"] == [2, 4]
    assert saved["settings"]["split"] == "always"
    assert saved["pages"][1]["crop"] == [200, 0, 400, 200]


@pytest.mark.parametrize("split", ["none", "always"])
def test_resume_noncontiguous_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_docling: list[list[int]],
    split: Split,
) -> None:
    pdf = make_spread(tmp_path / "spread.pdf", 17)
    paths = Paths(pdf, tmp_path / "chapter.md")
    selection = list(range(1, 18, 2))
    settings = Settings(
        pages=",".join(map(str, selection)), split=split, engine="docling"
    )
    extract = Docling.extract

    def interrupt(
        self: Docling,
        infos: list[PageInfo],
        engine: str,
        assets: Path,
        prefix: str,
    ) -> dict[int, PageResult]:
        if (split == "none" and infos[0].number == 17) or (
            split == "always" and fake_docling
        ):
            raise KeyboardInterrupt
        return extract(self, infos, engine, assets, prefix)

    monkeypatch.setattr(Docling, "extract", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pipeline.convert(paths, settings)
    checkpoint = json.loads(next(paths.work.glob("*/chunk-*.json")).read_text())
    assert len(checkpoint["pages"]) == (16 if split == "always" else 8)
    assert not paths.output.exists()
    monkeypatch.setattr(Docling, "extract", extract)
    report = pipeline.convert(paths, settings)
    assert report.resumed_pages == 8
    assert report.selected_pages == selection
    assert len(report.pages) == (18 if split == "always" else 9)
    assert len(fake_docling) == (2 if split == "always" else 9)
    assert not paths.work.exists()


@pytest.mark.parametrize("damage", ["missing", "duplicate", "reversed"])
def test_split_checkpoint_requires_both_halves(
    tmp_path: Path, fake_docling: list[list[int]], damage: str
) -> None:
    paths = Paths(make_spread(tmp_path / "spread.pdf"), tmp_path / "chapter.md")
    settings = Settings(split="always")

    def stop(*args: object) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        pipeline.convert(paths, settings, progress=stop)
    checkpoint = next(paths.work.glob("*/chunk-*.json"))
    saved = json.loads(checkpoint.read_text())
    if damage == "missing":
        saved["pages"].pop()
    elif damage == "duplicate":
        saved["pages"][1] = saved["pages"][0]
    else:
        saved["pages"].reverse()
    checkpoint.write_text(json.dumps(saved))
    report = pipeline.convert(paths, settings)
    assert report.resumed_pages == 0
    assert fake_docling == [[1, 2], [1, 2]]


@pytest.mark.parametrize(
    "settings", [Settings(pages="2"), Settings(pages="1", split="always")]
)
def test_selection_and_split_invalidate_checkpoints(
    tmp_path: Path, fake_docling: list[list[int]], settings: Settings
) -> None:
    paths = Paths(make_spread(tmp_path / "spread.pdf", 2), tmp_path / "chapter.md")

    def stop(*args: object) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        pipeline.convert(paths, Settings(pages="1"), progress=stop)
    assert pipeline.convert(paths, settings).resumed_pages == 0


def test_failed_half_preserves_all_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_docling: list[list[int]]
) -> None:
    paths = Paths(make_spread(tmp_path / "spread.pdf"), tmp_path / "chapter.md")
    paths.output.write_text("Previous Markdown")
    paths.report.write_text("Previous report")
    paths.assets.mkdir()
    (paths.assets / "previous.png").write_bytes(b"Previous figure")
    extract = Docling.extract

    def omit(
        self: Docling,
        infos: list[PageInfo],
        engine: str,
        assets: Path,
        prefix: str,
    ) -> dict[int, PageResult]:
        results = extract(self, infos, engine, assets, prefix)
        results.pop(2)
        return results

    monkeypatch.setattr(Docling, "extract", omit)
    with pytest.raises(ConversionError, match="omitted"):
        pipeline.convert(
            paths, Settings(split="always", engine="docling"), write_report=True
        )
    assert paths.output.read_text() == "Previous Markdown"
    assert paths.report.read_text() == "Previous report"
    assert (paths.assets / "previous.png").read_bytes() == b"Previous figure"
    assert not list(paths.work.glob("*/chunk-*.json"))


def test_blank_half_has_no_inference(
    tmp_path: Path, fake_docling: list[list[int]]
) -> None:
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "half-blank.pdf"
    document = canvas.Canvas(str(pdf), pagesize=(400, 200))
    document.drawString(20, 150, "Left research section.")
    document.save()
    report = pipeline.convert(
        Paths(pdf, tmp_path / "output.md"), Settings(split="always")
    )
    assert fake_docling == [[1]]
    assert report.pages[0].half == "left"
    assert not report.pages[0].blank
    assert report.pages[1].half == "right"
    assert report.pages[1].blank
    assert report.pages[1].engine == "none"
    assert not report.pages[1].attempts


def test_cli_page_options(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", 3)
    runner = CliRunner()
    result = runner.invoke(app, [str(pdf), "--pages", "3,1", "--report"])
    assert result.exit_code == 0, result.output
    assert "[1/2] Page 1" in result.output
    assert "[2/2] Page 3" in result.output
    assert "--pages" in runner.invoke(app, [str(pdf), "--pages", "4"]).output
    assert runner.invoke(app, [str(pdf), "--split", "bad"]).exit_code == 2
    conflict = runner.invoke(
        app, [str(pdf), "--split", "always", "--engine", "pdf-inspector"]
    )
    assert conflict.exit_code == 1
    assert "cannot process" in conflict.output
