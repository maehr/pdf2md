"""Sequential chunks, checkpoint recovery, and output assembly."""

import fcntl
import hashlib
import json
import re
import shutil
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from importlib.metadata import PackageNotFoundError, version
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, unquote, urlparse

from pydantic import ValidationError

from pdf2md.core import (
    boundary_warnings,
    choose_engine,
    fidelity_warnings,
    markdown_warnings,
    safe_asset,
    select_pages,
    validate_pages,
    validate_settings,
    validate_text,
)
from pdf2md.engines import Docling, Inspector
from pdf2md.models import (
    CHUNK_SIZE,
    MODEL_ID,
    Attempt,
    Checkpoint,
    ConversionError,
    ConversionReport,
    PageInfo,
    PageReport,
    PageResult,
    Paths,
    Settings,
    Split,
)

Progress = Callable[[int, int, str], None]


def versions() -> dict[str, str]:
    result = {}
    for package in (
        "pdf2md-local",
        "pdf-inspector",
        "docling-slim",
        "docling-core",
        "docling-ibm-models",
        "docling-parse",
        "ocrmac",
        "mlx",
        "mlx-vlm",
        "torch",
        "pypdfium2",
    ):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "unavailable"
    return result


def atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def check_assets(result: PageResult, directory: Path, prefix: str) -> None:
    for name in result.assets:
        if not safe_asset(name) or not (directory / name).is_file():
            raise ConversionError(
                f"Page {result.report.page}: missing or unsafe asset."
            )
        if (directory / name).is_symlink():
            raise ConversionError("Asset symlinks are not supported.")
    links = re.findall(r"!\[[^\]]*\]\(<?([^\s)>]+)", result.markdown)
    links += re.findall(r"<img\b[^>]*\bsrc=[\"\']([^\"\']+)", result.markdown)
    for link in links:
        if urlparse(link).scheme:
            result.report.warnings.append("Image link is not a local extracted asset.")
            continue
        name = unquote(link.removeprefix(prefix))
        if not link.startswith(prefix) or name not in result.assets:
            raise ConversionError(
                f"Page {result.report.page}: broken image link {link}."
            )


def read_checkpoint(
    path: Path,
    fingerprint: str,
    numbers: list[int],
    assets: Path,
    prefix: str,
    split: Split = "none",
) -> list[PageResult] | None:
    if not path.is_file():
        return None
    try:
        checkpoint = Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
        if checkpoint.fingerprint != fingerprint:
            return None
        validate_pages(checkpoint.pages, numbers, split)
        for page in checkpoint.pages:
            if split == "always" and len(page.report.crop) != 4:
                return None
            check_assets(page, assets, prefix)
            validate_text(
                page.markdown,
                PageInfo(
                    page.report.page, page.report.source, "", blank=page.report.blank
                ),
            )
        return checkpoint.pages
    except (OSError, ValidationError, ConversionError):
        return None


def extract_group(
    infos: list[PageInfo],
    engine: str,
    inspector: Inspector,
    docling: Docling,
    assets: Path,
    prefix: str,
    settings: Settings,
) -> list[PageResult]:
    if all(info.blank for info in infos):
        return [
            PageResult(
                markdown="",
                report=PageReport(
                    page=info.number,
                    source=info.source,
                    engine="none",
                    ocr=False,
                    vlm=False,
                    blank=True,
                ),
            )
            for info in infos
        ]
    started = time.perf_counter()
    try:
        if engine == "pdf-inspector":
            extracted = inspector.extract(infos, assets, prefix)
        else:
            extracted = docling.extract(infos, engine, assets, prefix)
        group_error = None
    except Exception as error:
        extracted = {}
        group_error = str(error)
    seconds = (time.perf_counter() - started) / len(infos)
    results = []
    for info in infos:
        error = group_error
        page = extracted.get(info.number)
        if page is not None:
            try:
                validate_text(page.markdown, info)
                check_assets(page, assets, prefix)
            except ConversionError as failure:
                error = str(failure)
        elif error is None:
            error = f"Page {info.number}: {engine} omitted the page."
        attempt = Attempt(engine=engine, seconds=seconds, error=error)
        if error is not None:
            if settings.engine != "auto" or engine == "docling":
                raise ConversionError(f"Page {info.number}: {engine}: {error}")
            fallback = extract_group(
                [info], "docling", inspector, docling, assets, prefix, settings
            )[0]
            fallback.report.attempts.insert(0, attempt)
            fallback.report.warnings.append(f"{engine} failed: {error}")
            results.append(fallback)
        elif page is not None:
            page.report.attempts.append(attempt)
            page.report.warnings.extend(markdown_warnings(page.markdown))
            if not info.needs_ocr:
                page.report.warnings.extend(
                    fidelity_warnings(info.native_text, page.markdown)
                )
            results.append(page)
    return results


def extract_chunk(
    numbers: list[int],
    settings: Settings,
    inspector: Inspector,
    docling: Docling,
    assets: Path,
    prefix: str,
) -> list[PageResult]:
    if settings.split == "always":
        with TemporaryDirectory(prefix="split-", dir=assets.parent) as temporary:
            path = Path(temporary) / "halves.pdf"
            parts = inspector.split_pages(numbers, path)
            with closing(Inspector(path)) as split_inspector:
                results = extract_chunk(
                    list(range(1, len(parts) + 1)),
                    settings.model_copy(update={"split": "none"}),
                    split_inspector,
                    docling.for_path(path),
                    assets,
                    prefix,
                )
            validate_pages(results, len(parts))
            for result, part in zip(results, parts, strict=True):
                result.report.page = part.page
                result.report.half = part.half
                result.report.crop = list(part.crop)
                for provenance in result.report.provenance:
                    provenance.page = part.page
                    if provenance.bbox:
                        left, top, right, bottom = provenance.bbox
                        provenance.bbox = [
                            left + part.crop[0],
                            top + part.crop[1],
                            right + part.crop[0],
                            bottom + part.crop[1],
                        ]
            return results
    infos = inspector.inspect(numbers, settings)
    results = []
    groups = groupby(
        enumerate(infos),
        key=lambda item: (
            choose_engine(item[1], settings),
            item[1].needs_ocr,
            item[1].blank,
            item[1].number - item[0],
        ),
    )
    for (engine, _, _, _), group in groups:
        results.extend(
            extract_group(
                [info for _, info in group],
                engine,
                inspector,
                docling,
                assets,
                prefix,
                settings,
            )
        )
    return results


def chunks(numbers: list[int]) -> Iterator[list[int]]:
    for start in range(0, len(numbers), CHUNK_SIZE):
        yield numbers[start : start + CHUNK_SIZE]


def publish(paths: Paths, markdown: Path, report: Path | None, assets: Path) -> None:
    """Keep previous outputs available if publication raises an error."""
    candidates = []
    if any(assets.iterdir()) or paths.assets.exists():
        candidates.append((assets, paths.assets))
    if report is not None:
        candidates.append((report, paths.report))
    candidates.append((markdown, paths.output))
    completed: list[tuple[Path, Path, Path | None]] = []
    try:
        for source, destination in candidates:
            backup = source.with_name(source.name + ".previous")
            old = None
            if destination.exists():
                destination.replace(backup)
                old = backup
            completed.append((source, destination, old))
            source.replace(destination)
    except OSError:
        for source, destination, old in reversed(completed):
            if destination.exists():
                destination.replace(source)
            if old is not None:
                old.replace(destination)
        raise


def convert(
    paths: Paths,
    settings: Settings,
    *,
    write_report: bool = False,
    progress: Progress | None = None,
) -> ConversionReport:
    """Convert one PDF, with bounded image memory and reusable chunks."""
    validate_settings(settings)
    paths = Paths(paths.input.resolve(), paths.output.absolute())
    if paths.input == paths.output.resolve():
        raise ConversionError("Input and output paths must differ.")
    if paths.output.suffix.lower() != ".md":
        raise ConversionError("The output path must end with .md.")
    if not paths.input.is_file():
        raise ConversionError(f"Input does not exist: {paths.input}")
    for path in (paths.output, paths.report, paths.assets, paths.work):
        if path.is_symlink():
            raise ConversionError(f"Output symlinks are not supported: {path}")
    if paths.output.is_dir() or paths.report.is_dir():
        raise ConversionError("An output file path names a directory.")
    if paths.assets.exists() and not paths.assets.is_dir():
        raise ConversionError("The asset path must name a directory.")
    paths.output.parent.mkdir(parents=True, exist_ok=True)
    paths.work.mkdir(exist_ok=True)
    owner = paths.work / "owner"
    if not owner.exists():
        if any(paths.work.iterdir()):
            raise ConversionError("The work directory contains unrelated files.")
        owner.write_text("pdf2md-work-v1", encoding="utf-8")
    if owner.read_text(encoding="utf-8") != "pdf2md-work-v1":
        raise ConversionError("The work directory belongs to another application.")
    with (paths.work / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ConversionError("Another conversion uses this output.") from error
        inspector = Inspector(paths.input)
        try:
            result = run(paths, settings, inspector, write_report, progress)
        finally:
            inspector.close()
        shutil.rmtree(paths.work)
        return result


def run(
    paths: Paths,
    settings: Settings,
    inspector: Inspector,
    write_report: bool,
    progress: Progress | None,
) -> ConversionReport:
    started = time.perf_counter()
    count = inspector.page_count
    if count == 0:
        raise ConversionError("The PDF has no pages.")
    selected = select_pages(settings.pages, count)
    work_count = len(selected) * (2 if settings.split == "always" else 1)
    with paths.input.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    installed = versions()
    docling = Docling(paths.input)
    uses_vlm = settings.engine == "vlm" or (
        settings.engine == "auto" and settings.quality == "best"
    )
    if uses_vlm:
        if progress:
            progress(0, work_count, "Prepare cached MLX model")
        docling.prepare_vlm()
    identity = {
        "input": digest,
        "settings": settings.model_dump(),
        "versions": installed,
        "model_revision": docling.model_revision,
        "chunk_size": CHUNK_SIZE,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()
    directory = paths.work / fingerprint
    directory.mkdir(exist_ok=True)
    assets = directory / "assets"
    assets.mkdir(exist_ok=True)
    prefix = quote(paths.assets.name) + "/"
    pages = []
    resumed = 0
    completed = 0
    warnings: list[str] = []
    previous = ""
    markdown_path = directory / "output.md"
    with markdown_path.open("w", encoding="utf-8") as markdown_file:
        for numbers in chunks(selected):
            checkpoint_path = directory / f"chunk-{numbers[0]:06}.json"
            results = read_checkpoint(
                checkpoint_path, fingerprint, numbers, assets, prefix, settings.split
            )
            cached = results is not None
            if results is None:
                results = extract_chunk(
                    numbers, settings, inspector, docling, assets, prefix
                )
                validate_pages(results, numbers, settings.split)
                atomic_text(
                    checkpoint_path,
                    Checkpoint(
                        fingerprint=fingerprint, pages=results
                    ).model_dump_json(),
                )
            else:
                resumed += len(numbers)
            for page in results:
                number = page.report.page
                page.report.warnings.extend(boundary_warnings(previous, page.markdown))
                if page.markdown.strip():
                    markdown_file.write(page.markdown.strip() + "\n\n")
                    previous = page.markdown
                pages.append(page.report)
                completed += 1
                if progress:
                    label = "cached" if cached else page.report.engine
                    half = f" {page.report.half}" if page.report.half else ""
                    progress(completed, work_count, f"Page {number}{half}: {label}")
    # Validate lightweight page records without retaining all Markdown in memory.
    validate_pages(
        (PageResult(markdown="", report=page) for page in pages),
        selected,
        settings.split,
    )
    sources = {page.source for page in pages if not page.blank} or {
        page.source for page in pages
    }
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    report = ConversionReport(
        input=str(paths.input),
        output=str(paths.output),
        input_sha256=digest,
        page_count=count,
        selected_pages=selected,
        source=source,
        settings=settings,
        versions=installed,
        model=MODEL_ID if uses_vlm else None,
        model_revision=docling.model_revision,
        seconds=time.perf_counter() - started,
        resumed_pages=resumed,
        warnings=warnings,
        pages=pages,
    )
    report_path = directory / "report.json" if write_report else None
    if report_path is not None:
        atomic_text(report_path, report.model_dump_json(indent=2))
    publish(paths, markdown_path, report_path, assets)
    return report
