"""Command line entry point."""

from pathlib import Path
from typing import Annotated

import typer

from pdf2md.models import (
    ConversionError,
    Engine,
    Paths,
    Quality,
    Settings,
    Source,
    Split,
)
from pdf2md.pipeline import convert

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@app.command()
def main(
    input_pdf: Annotated[Path, typer.Argument(help="PDF to convert.")],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    source: Annotated[Source, typer.Option()] = "auto",
    quality: Annotated[Quality, typer.Option()] = "fast",
    engine: Annotated[Engine, typer.Option()] = "auto",
    pages: Annotated[
        str | None, typer.Option(help="Physical PDF pages, such as 1,3,7-9.")
    ] = None,
    split: Annotated[
        Split, typer.Option(help="Split selected pages into left and right halves.")
    ] = "none",
    preserve_pages: Annotated[
        bool, typer.Option(help="Insert silent physical page markers.")
    ] = True,
    report: Annotated[bool, typer.Option(help="Write a JSON report.")] = False,
) -> None:
    """Convert a PDF into local Markdown, figures, and optional provenance."""
    settings = Settings(
        source=source,
        quality=quality,
        engine=engine,
        pages=pages,
        split=split,
        preserve_pages=preserve_pages,
    )

    def progress(page: int, count: int, label: str) -> None:
        typer.echo(f"[{page}/{count}] {label}", err=True)

    try:
        result = convert(
            Paths(input_pdf, output or input_pdf.with_suffix(".md")),
            settings,
            write_report=report,
            progress=progress,
        )
    except (ConversionError, OSError, ValueError, RuntimeError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from error
    except KeyboardInterrupt as error:
        typer.echo(
            "Interrupted. Repeat the command to resume completed chunks.", err=True
        )
        raise typer.Exit(130) from error
    warnings = result.warnings + [w for page in result.pages for w in page.warnings]
    if warnings:
        typer.echo(f"Warnings: {len(warnings)}. Verify the source pages.", err=True)
        for warning in dict.fromkeys(warnings):
            typer.echo(f"  {warning}", err=True)
    typer.echo(Path(result.output).resolve())


if __name__ == "__main__":
    app()
