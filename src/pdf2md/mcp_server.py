"""Optional stdio MCP boundary for the local conversion pipeline."""

import logging
import os
import stat
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from pdf2md import __version__
from pdf2md.models import Engine, Paths, Quality, Settings, Source
from pdf2md.pipeline import convert

if TYPE_CHECKING:
    from fastmcp import FastMCP

_CONVERSION_LOCK = Lock()
_LOGGER = logging.getLogger(__name__)
_INSTALL_HELP = "Install the MCP extra with: uv sync --locked --extra mcp"


class ConversionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversion_id: str
    input_pdf: str
    output_markdown: str
    assets_directory: str | None
    report_json: str
    page_count: int
    resumed_pages: int
    seconds: float
    warnings_total: int
    warnings: list[str]
    warnings_omitted: int


class MarkdownChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversion_id: str
    markdown: str
    offset_bytes: int
    next_offset_bytes: int
    eof: bool


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> "_FileIdentity":
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("The Markdown result must be a regular file.")
        return cls(
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )


@dataclass(frozen=True)
class _Published:
    path: Path
    identity: _FileIdentity


@dataclass(frozen=True)
class _Root:
    path: Path
    device: int
    inode: int

    @classmethod
    def create(cls, root: Path) -> "_Root":
        path = root.resolve(strict=True)
        if not path.is_dir():
            raise ValueError("PDF2MD_MCP_ROOT must name an existing directory.")
        metadata = path.stat()
        return cls(path, metadata.st_dev, metadata.st_ino)

    def check(self) -> None:
        metadata = self.path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (self.device, self.inode)
            or self.path.resolve(strict=True) != self.path
        ):
            raise ValueError("The configured MCP root changed. Restart the server.")

    def resolve(self, value: str) -> Path:
        self.check()
        if not value or "\x00" in value or urlsplit(value).scheme:
            raise ValueError("Use a local file path without a URL or scheme.")
        path = Path(value)
        if not path.is_absolute():
            path = self.path / path
        for component in (path, *path.parents):
            if component.is_symlink():
                raise ValueError("Symlinks are not supported in file paths.")
        resolved = path.resolve()
        try:
            resolved.relative_to(self.path)
        except ValueError:
            raise ValueError("The path is outside the configured MCP root.") from None
        return resolved


def _validate_paths(root: _Root, paths: Paths, overwrite: bool) -> None:
    if paths.input.suffix.lower() != ".pdf" or not paths.input.is_file():
        raise ValueError("The input must be an existing regular .pdf file.")
    if paths.output.suffix != ".md":
        raise ValueError("The output path must end with .md.")
    for path in (paths.output, paths.report, paths.assets, paths.work):
        root.resolve(str(path))
    for path in (paths.output, paths.report):
        if path.exists() and not path.is_file():
            raise ValueError("An output file target is not a regular file.")
    for path in (paths.assets, paths.work):
        if path.exists() and not path.is_dir():
            raise ValueError("Asset and work targets must name directories.")
    if not overwrite and any(
        path.exists() for path in (paths.output, paths.report, paths.assets)
    ):
        raise ValueError("Output already exists. Use overwrite=True to replace it.")
    # Checkpoints contain pipeline-controlled filenames, including a writable lock.
    # Reject links and special files before the pipeline reads or opens them.
    if paths.work.exists():
        for path in paths.work.rglob("*"):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("The work directory contains an unsafe target.")


def _read_chunk(
    root: _Root, published: _Published, offset_bytes: int, max_bytes: int
) -> tuple[str, int, bool]:
    if offset_bytes < 0:
        raise ValueError("offset_bytes must be at least 0.")
    if not 4 <= max_bytes <= 16384:
        raise ValueError("max_bytes must be between 4 and 16384.")
    path = root.resolve(str(published.path))
    # O_NOFOLLOW also rejects replacement of the final target during validation.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb", buffering=0) as stream:
        identity = _FileIdentity.from_stat(os.fstat(stream.fileno()))
        if identity != published.identity:
            raise ValueError("The Markdown result changed. Convert the PDF again.")
        if offset_bytes > identity.size:
            raise ValueError("offset_bytes exceeds the Markdown file size.")
        stream.seek(offset_bytes)
        data = stream.read(max_bytes)
        if data and data[0] & 0xC0 == 0x80:
            raise ValueError("offset_bytes starts inside a UTF-8 sequence.")
        try:
            markdown = data.decode("utf-8")
        except UnicodeDecodeError as error:
            if (
                error.reason != "unexpected end of data"
                or offset_bytes + len(data) == identity.size
            ):
                raise ValueError(
                    "The Markdown result contains invalid UTF-8."
                ) from error
            data = data[: error.start]
            markdown = data.decode("utf-8")
        if _FileIdentity.from_stat(os.fstat(stream.fileno())) != identity:
            raise ValueError("The Markdown result changed during the read.")
        if (
            root.resolve(str(path)) != path
            or _FileIdentity.from_stat(path.stat(follow_symlinks=False)) != identity
        ):
            raise ValueError("The Markdown result changed during the read.")
    next_offset = offset_bytes + len(data)
    return markdown, next_offset, next_offset == identity.size


def create_server(root: Path) -> "FastMCP":
    """Create a local server with a private registry of published results."""
    try:
        from fastmcp import FastMCP
        from fastmcp.exceptions import ToolError
    except ImportError as error:
        raise RuntimeError(_INSTALL_HELP) from error

    policy = _Root.create(root)
    registry: dict[str, _Published] = {}
    server = FastMCP(
        "pdf2md",
        version=__version__,
        instructions=(
            "Convert one local PDF within the configured root. "
            "All conversion and inference run locally. "
            "Read results with read_markdown and next_offset_bytes. "
            "Conversion IDs expire when this server process exits."
        ),
        mask_error_details=True,
    )

    @server.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        }
    )
    def convert_pdf(
        input_pdf: str,
        output_markdown: str | None = None,
        source: Source = "auto",
        quality: Quality = "fast",
        engine: Engine = "auto",
        overwrite: bool = False,
    ) -> ConversionSummary:
        """Convert one local PDF and write Markdown, assets, and a JSON report.

        Relative paths start at the configured root.
        overwrite=True can replace existing output files.
        Client timeouts do not guarantee cancellation.
        """
        if not _CONVERSION_LOCK.acquire(blocking=False):
            raise ToolError("Another PDF conversion is active. Retry when it finishes.")
        try:
            input_path = policy.resolve(input_pdf)
            output_path = policy.resolve(
                output_markdown
                if output_markdown is not None
                else str(input_path.with_suffix(".md"))
            )
            paths = Paths(input_path, output_path)
            settings = Settings(source=source, quality=quality, engine=engine)
            _validate_paths(policy, paths, overwrite)
            # The stdio transport captures its protocol stream before tool execution.
            # Keep engine diagnostics away from that stream.
            with redirect_stdout(sys.stderr):
                report = convert(paths, settings, write_report=True)
            policy.resolve(str(output_path))
            identity = _FileIdentity.from_stat(output_path.stat())
            warnings = list(
                dict.fromkeys(
                    chain(
                        report.warnings,
                        chain.from_iterable(page.warnings for page in report.pages),
                    )
                )
            )
            conversion_id = str(uuid4())
            summary = ConversionSummary(
                conversion_id=conversion_id,
                input_pdf=str(input_path),
                output_markdown=str(output_path),
                assets_directory=str(paths.assets) if paths.assets.is_dir() else None,
                report_json=str(paths.report),
                page_count=report.page_count,
                resumed_pages=report.resumed_pages,
                seconds=report.seconds,
                warnings_total=len(warnings),
                warnings=warnings[:20],
                warnings_omitted=max(0, len(warnings) - 20),
            )
            registry[conversion_id] = _Published(output_path, identity)
            return summary
        except Exception as error:
            _LOGGER.error("PDF conversion failed (%s).", type(error).__name__)
            raise ToolError(f"PDF conversion failed: {error}") from None
        finally:
            _CONVERSION_LOCK.release()

    @server.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    def read_markdown(
        conversion_id: str, offset_bytes: int = 0, max_bytes: int = 16384
    ) -> MarkdownChunk:
        """Read a bounded UTF-8 section from a successful conversion.

        Use next_offset_bytes for the next section.
        Conversion IDs expire when the MCP process exits.
        """
        published = registry.get(conversion_id)
        if published is None:
            raise ToolError("Unknown or expired conversion ID. Convert the PDF first.")
        try:
            markdown, next_offset, eof = _read_chunk(
                policy, published, offset_bytes, max_bytes
            )
            return MarkdownChunk(
                conversion_id=conversion_id,
                markdown=markdown,
                offset_bytes=offset_bytes,
                next_offset_bytes=next_offset,
                eof=eof,
            )
        except (OSError, ValueError, RuntimeError) as error:
            raise ToolError(f"Cannot read Markdown: {error}") from None

    return server


def main() -> None:
    """Start the optional server over stdio only."""
    try:
        root_value = os.environ.get("PDF2MD_MCP_ROOT")
        if not root_value:
            raise ValueError("PDF2MD_MCP_ROOT must be set to an existing directory.")
        server = create_server(Path(root_value))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
