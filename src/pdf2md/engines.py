"""Adapters for pdf-inspector and Docling. Models load only when requested."""

import platform
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import pdf_inspector
import pypdfium2 as pdfium

from pdf2md.core import (
    classify_source,
    repeated_margins,
    strip_margins,
    text_warnings,
)
from pdf2md.models import (
    ConversionError,
    PageInfo,
    PagePart,
    PageReport,
    PageResult,
    Provenance,
    Settings,
)

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import DoclingDocument


class Inspector:
    """Keep one PDF open and inspect only the current chunk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.pdf = pdfium.PdfDocument(str(path))
        except Exception as error:
            raise ConversionError(f"Cannot open PDF: {error}") from error
        self.markdown: dict[int, str] = {}

    @property
    def page_count(self) -> int:
        return len(self.pdf)

    def close(self) -> None:
        self.pdf.close()

    def split_pages(self, numbers: list[int], destination: Path) -> list[PagePart]:
        """Render each displayed half at 144 DPI for local OCR and VLM input."""
        parts = []
        with pdfium.PdfDocument.new() as document:
            for number in numbers:
                with closing(self.pdf[number - 1]) as source:
                    width, height = source.get_size()
                    for half, left, right in (
                        ("left", 0.0, width / 2),
                        ("right", width / 2, width),
                    ):
                        with (
                            closing(
                                source.render(scale=2, crop=(left, 0, width - right, 0))
                            ) as bitmap,
                            closing(document.new_page(right - left, height)) as page,
                        ):
                            image = pdfium.PdfImage.new(document)
                            image.set_bitmap(bitmap)
                            image.set_matrix(
                                pdfium.PdfMatrix(right - left, 0, 0, height)
                            )
                            page.insert_obj(image)
                            page.gen_content()
                        parts.append(PagePart(number, half, (left, 0.0, right, height)))
            document.save(destination)
        return parts

    def inspect(self, numbers: list[int], settings: Settings) -> list[PageInfo]:
        inspection_error = None
        try:
            extraction = pdf_inspector.extract_pages_markdown(
                str(self.path), pages=[n - 1 for n in numbers]
            )
            native = {page.page + 1: page for page in extraction.pages}
        except Exception as error:
            extraction = None
            native = {}
            inspection_error = f"Native inspection failed: {error}"
        self.markdown = {n: native[n].markdown if n in native else "" for n in numbers}
        infos = []
        margins: dict[int, set[str]] = {}
        for number in numbers:
            page = self.pdf[number - 1]
            try:
                with closing(page.get_textpage()) as textpage:
                    text = textpage.get_text_bounded()
                    height = page.get_height()
                    margin_text = (
                        textpage.get_text_bounded(bottom=height * 0.92)
                        + "\n"
                        + textpage.get_text_bounded(top=height * 0.08)
                    )
                    margins[number] = {
                        line.strip()
                        for line in margin_text.splitlines()
                        if line.strip()
                    }
                width, height = page.get_size()
                area = 0.0
                for image in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
                    left, bottom, right, top = image.get_bounds()
                    area += max(0, right - left) * max(0, top - bottom)
                coverage = min(1.0, area / max(1, width * height))
                blank = False
                if not text.strip():
                    with closing(page.render(scale=0.25)) as bitmap:
                        blank = bitmap.to_pil().convert("L").getextrema()[0] == 255
                warnings = text_warnings(text)
                needs_ocr = (
                    number not in native or native[number].needs_ocr or bool(warnings)
                )
                if inspection_error:
                    warnings.append(inspection_error)
                if number in native and native[number].ocr_reason:
                    warnings.append(f"Native text: {native[number].ocr_reason}.")
                if settings.source in {"scan", "ocred"}:
                    needs_ocr = True
                infos.append(
                    PageInfo(
                        number=number,
                        source=classify_source(text, coverage, settings.source),
                        native_text=text,
                        needs_ocr=needs_ocr and not blank,
                        complex_layout=(
                            extraction is None
                            or number in extraction.pages_with_tables
                            or number in extraction.pages_with_columns
                            or coverage > 0
                        ),
                        blank=blank,
                        image_coverage=coverage,
                        warnings=warnings,
                    )
                )
            finally:
                page.close()
        repeated = repeated_margins(list(margins.values()))
        for number in numbers:
            self.markdown[number] = strip_margins(
                self.markdown[number], margins[number], repeated
            )
        return infos

    def extract(
        self, infos: list[PageInfo], assets: Path, asset_prefix: str
    ) -> dict[int, PageResult]:
        results = {}
        for info in infos:
            if info.needs_ocr:
                raise ConversionError(
                    f"Page {info.number}: pdf-inspector requires OCR."
                )
            markdown = self.markdown[info.number]
            names = []
            warnings = list(info.warnings)
            page = self.pdf[info.number - 1]
            try:
                for index, image in enumerate(
                    page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]), 1
                ):
                    name = f"native-p{info.number}-{index}.png"
                    with closing(image.get_bitmap()) as bitmap:
                        bitmap.to_pil().save(assets / name)
                    names.append(name)
                    markdown += f"\n\n![Image]({asset_prefix}{quote(name)})"
            finally:
                page.close()
            if names:
                warnings.append("Native images follow page text. Verify caption order.")
            results[info.number] = PageResult(
                markdown=markdown,
                assets=names,
                report=PageReport(
                    page=info.number,
                    source=info.source,
                    engine="pdf-inspector",
                    ocr=False,
                    vlm=False,
                    blank=info.blank,
                    warnings=warnings,
                    provenance=[Provenance(page=info.number, label="native_page")],
                ),
            )
        return results


class Docling:
    """Reuse a converter for each engine and OCR configuration."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.converters: dict[tuple[str, bool], DocumentConverter] = {}
        self.model_revision: str | None = None
        self.vlm_error: str | None = None

    def for_path(self, path: Path) -> "Docling":
        """Reuse loaded models for a temporary split document."""
        adapter = Docling(path)
        adapter.converters = self.converters
        adapter.model_revision = self.model_revision
        adapter.vlm_error = self.vlm_error
        return adapter

    def prepare_vlm(self) -> None:
        from huggingface_hub import snapshot_download

        from pdf2md.models import MODEL_ID, MODEL_REVISION

        try:
            if platform.system() != "Darwin" or platform.machine() != "arm64":
                raise ConversionError("MLX requires Apple Silicon macOS.")
            model_path = snapshot_download(
                MODEL_ID,
                revision=MODEL_REVISION,
            )
            self.model_revision = Path(model_path).name
        except Exception as error:
            self.vlm_error = str(error)

    def converter(self, engine: str, ocr: bool) -> "DocumentConverter":
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            OcrMacOptions,
            OcrMode,
            PdfPipelineOptions,
            VlmConvertOptions,
            VlmPipelineOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.pipeline.vlm_pipeline import VlmPipeline

        key = (engine, ocr)
        if key in self.converters:
            return self.converters[key]
        if engine == "vlm":
            if self.vlm_error:
                raise ConversionError(self.vlm_error)
            if platform.system() != "Darwin" or platform.machine() != "arm64":
                raise ConversionError(
                    "MLX VLM conversion requires Apple Silicon macOS."
                )
            from docling.datamodel.vlm_engine_options import MlxVlmEngineOptions

            from pdf2md.models import MODEL_REVISION

            vlm = VlmConvertOptions.from_preset(
                "granite_docling", engine_options=MlxVlmEngineOptions()
            )
            vlm.model_spec = vlm.model_spec.model_copy(
                deep=True, update={"revision": self.model_revision or MODEL_REVISION}
            )

            option = PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=VlmPipelineOptions(
                    vlm_options=vlm,
                    enable_remote_services=False,
                    generate_picture_images=True,
                ),
            )
        else:
            import torch

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            option = PdfFormatOption(
                pipeline_options=PdfPipelineOptions(
                    accelerator_options=AcceleratorOptions(device=device),
                    enable_remote_services=False,
                    do_ocr=ocr,
                    ocr_options=OcrMacOptions(
                        mode=OcrMode.FULL_PAGE if ocr else OcrMode.DEFAULT,
                        lang=[],
                        recognition="accurate",
                    ),
                    do_formula_enrichment=False,
                    do_code_enrichment=False,
                    generate_page_images=True,
                    generate_picture_images=True,
                )
            )
        converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: option},
        )
        self.converters[key] = converter
        return converter

    def extract(
        self,
        infos: list[PageInfo],
        engine: str,
        assets: Path,
        asset_prefix: str,
    ) -> dict[int, PageResult]:
        from docling.datamodel.base_models import ConversionStatus
        from docling_core.types.doc import ContentLayer, DocItemLabel, ImageRefMode

        ocr = any(info.needs_ocr for info in infos)
        converter = self.converter(engine, ocr)
        conversion = converter.convert(
            self.path,
            page_range=(infos[0].number, infos[-1].number),
            raises_on_error=False,
        )
        if conversion.status not in {
            ConversionStatus.SUCCESS,
            ConversionStatus.PARTIAL_SUCCESS,
        }:
            raise ConversionError(f"{engine}: {conversion.status.value}.")
        doc = conversion.document
        margins: dict[int, set[str]] = {info.number: set() for info in infos}
        for item in doc.texts:
            if item.label in {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}:
                for prov in item.prov:
                    if prov.page_no in margins:
                        margins[prov.page_no].add(item.text.strip())
        repeated = repeated_margins(list(margins.values()))
        results = {}
        for info in infos:
            warnings = list(info.warnings)
            if conversion.status == ConversionStatus.PARTIAL_SUCCESS:
                warnings.append("Docling reported partial success. Verify this page.")
            markdown = doc.export_to_markdown(
                page_no=info.number,
                image_mode=ImageRefMode.REFERENCED,
                image_dir=assets,
                image_uri_prefix=asset_prefix,
                included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE},
                labels=set(DocItemLabel),
                traverse_pictures=True,
                include_annotations=False,
            )
            markdown = strip_margins(markdown, margins[info.number], repeated)
            for table in doc.tables:
                if any(prov.page_no == info.number for prov in table.prov) and any(
                    cell.row_span > 1 or cell.col_span > 1
                    for cell in table.data.table_cells
                ):
                    markdown = markdown.replace(
                        table.export_to_markdown(doc), table.export_to_html(doc)
                    )
            names = [
                path.name
                for path in assets.iterdir()
                if f"{asset_prefix}{quote(path.name)}" in markdown
            ]
            results[info.number] = PageResult(
                markdown=markdown,
                assets=names,
                report=PageReport(
                    page=info.number,
                    source=info.source,
                    engine=engine,
                    ocr=engine == "docling" and ocr,
                    vlm=engine == "vlm",
                    blank=info.blank,
                    warnings=warnings,
                    provenance=self.provenance(doc, info.number),
                ),
            )
        return results

    @staticmethod
    def provenance(doc: "DoclingDocument", number: int) -> list[Provenance]:
        from docling_core.types.doc import ContentLayer, DocItem

        return [
            Provenance(
                page=number,
                label=item.label.value,
                bbox=list(
                    prov.bbox.to_top_left_origin(
                        doc.pages[number].size.height
                    ).as_tuple()
                ),
            )
            for item, _ in doc.iterate_items(
                traverse_pictures=True,
                included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE},
            )
            if isinstance(item, DocItem)
            for prov in item.prov
            if prov.page_no == number
        ]
