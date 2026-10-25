"""Small data contracts for conversion and recovery."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["auto", "digital", "ocred", "scan"]
Engine = Literal["auto", "pdf-inspector", "docling", "vlm"]
Quality = Literal["fast", "best"]
Split = Literal["none", "always"]
Half = Literal["left", "right"]

CHUNK_SIZE = 8
MODEL_ID = "ibm-granite/granite-docling-258M-mlx"
MODEL_REVISION = "e9939db25d2f296c8678d0491c4609a8c596c50a"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Source = "auto"
    quality: Quality = "fast"
    engine: Engine = "auto"
    pages: str | None = None
    split: Split = "none"


@dataclass(frozen=True)
class PagePart:
    page: int
    half: Half
    crop: tuple[float, float, float, float]


@dataclass
class PageInfo:
    number: int
    source: str
    native_text: str
    needs_ocr: bool = False
    complex_layout: bool = False
    blank: bool = False
    image_coverage: float = 0.0
    warnings: list[str] = field(default_factory=list)


class Attempt(BaseModel):
    engine: str
    seconds: float
    error: str | None = None


class Provenance(BaseModel):
    page: int
    label: str
    bbox: list[float] = Field(default_factory=list)


class PageReport(BaseModel):
    page: int
    half: Half | None = None
    crop: list[float] = Field(default_factory=list)
    source: str
    engine: str
    ocr: bool
    vlm: bool
    blank: bool = False
    warnings: list[str] = Field(default_factory=list)
    attempts: list[Attempt] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


class PageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str
    report: PageReport
    assets: list[str] = Field(default_factory=list)


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    pages: list[PageResult]


class ConversionReport(BaseModel):
    schema_version: int = 2
    input: str
    output: str
    input_sha256: str
    page_count: int
    selected_pages: list[int]
    source: str
    settings: Settings
    versions: dict[str, str]
    model: str | None
    model_revision: str | None
    seconds: float
    resumed_pages: int
    warnings: list[str]
    pages: list[PageReport]


@dataclass
class Paths:
    input: Path
    output: Path

    @property
    def assets(self) -> Path:
        return self.output.with_suffix(".assets")

    @property
    def report(self) -> Path:
        return self.output.with_suffix(".report.json")

    @property
    def work(self) -> Path:
        return self.output.parent / f".{self.output.name}.pdf2md-work"


class ConversionError(Exception):
    """A conversion cannot safely produce the requested output."""
