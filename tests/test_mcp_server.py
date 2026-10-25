"""MCP contracts and filesystem protection without model downloads."""

import asyncio
import json
import os
import sys
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from conftest import make_pdf

from pdf2md import __version__, mcp_server
from pdf2md.engines import Inspector
from pdf2md.models import PageInfo, Paths, Settings

fastmcp = pytest.importorskip("fastmcp", reason="Install the optional mcp extra.")


async def call(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with fastmcp.Client(server) as client:
        result = await client.call_tool(name, arguments)
        assert not result.is_error
        assert result.structured_content is not None
        return result.structured_content


async def error(server: Any, name: str, arguments: dict[str, Any]) -> str:
    async with fastmcp.Client(server) as client:
        result = await client.call_tool(name, arguments, raise_on_error=False)
        assert result.is_error
        assert result.structured_content is None
        return " ".join(block.text for block in result.content if block.type == "text")


def test_tool_schemas(tmp_path: Path) -> None:
    async def check() -> None:
        async with fastmcp.Client(mcp_server.create_server(tmp_path)) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert set(tools) == {"convert_pdf", "read_markdown"}
            convert = tools["convert_pdf"]
            schema = convert.input_schema
            assert schema["required"] == ["input_pdf"]
            properties = schema["properties"]
            for field, values, default in (
                ("source", ["auto", "digital", "ocred", "scan"], "auto"),
                ("quality", ["fast", "best"], "fast"),
                ("engine", ["auto", "pdf-inspector", "docling", "vlm"], "auto"),
            ):
                assert properties[field]["enum"] == values
                assert properties[field]["default"] == default
            assert properties["overwrite"]["default"] is False
            assert properties["output_markdown"]["default"] is None
            assert convert.annotations.read_only_hint is False
            assert convert.annotations.destructive_hint is True
            assert convert.annotations.open_world_hint is False
            assert (
                convert.output_schema["properties"]["conversion_id"]["type"] == "string"
            )
            read = tools["read_markdown"]
            assert read.annotations.read_only_hint is True
            assert read.input_schema["properties"]["max_bytes"]["default"] == 16384
            assert read.input_schema["properties"]["offset_bytes"]["default"] == 0
            assert read.output_schema["properties"]["markdown"]["type"] == "string"
            assert await client.list_resources() == []
            assert await client.list_prompts() == []

    asyncio.run(check())


@pytest.mark.parametrize("custom", [False, True])
def test_conversion_and_read(pdf: Path, tmp_path: Path, custom: bool) -> None:
    server = mcp_server.create_server(tmp_path)
    arguments: dict[str, Any] = {"input_pdf": "paper.pdf"}
    output = pdf.with_suffix(".md")
    if custom:
        output = tmp_path / "nested" / "custom.md"
        arguments["input_pdf"] = str(pdf)
        arguments["output_markdown"] = "nested/custom.md"
    summary = asyncio.run(call(server, "convert_pdf", arguments))
    assert summary["input_pdf"] == str(pdf.resolve())
    assert summary["output_markdown"] == str(output.resolve())
    assert summary["report_json"] == str(output.with_suffix(".report.json").resolve())
    assert summary["assets_directory"] is None
    assert summary["page_count"] == 1
    assert summary["resumed_pages"] == 0
    assert summary["seconds"] >= 0
    report = json.loads(output.with_suffix(".report.json").read_text())
    assert report["schema_version"] == 2
    assert report["versions"]["pdf2md-local"] == __version__
    chunk = asyncio.run(
        call(server, "read_markdown", {"conversion_id": summary["conversion_id"]})
    )
    assert "Unique page marker 0001" in chunk["markdown"]
    assert chunk["markdown"].encode() == output.read_bytes()
    assert chunk["next_offset_bytes"] == output.stat().st_size
    assert chunk["eof"]


def test_nested_input_and_real_assets(tmp_path: Path) -> None:
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    nested = tmp_path / "nested"
    nested.mkdir()
    pdf = nested / "figure.pdf"
    document = canvas.Canvas(str(pdf))
    for index in range(5):
        document.drawString(40, 750 - index * 20, "An academic figure and its caption.")
    document.drawImage(ImageReader(Image.new("RGB", (30, 30), "blue")), 40, 200, 50, 50)
    document.save()
    summary = asyncio.run(
        call(
            mcp_server.create_server(tmp_path),
            "convert_pdf",
            {
                "input_pdf": "nested/figure.pdf",
                "output_markdown": "out/custom.md",
                "engine": "pdf-inspector",
            },
        )
    )
    assert summary["assets_directory"] == str(tmp_path / "out" / "custom.assets")
    assert (Path(summary["assets_directory"]) / "native-p1-1.png").is_file()
    assert (
        "custom.assets/native-p1-1.png" in Path(summary["output_markdown"]).read_text()
    )


@pytest.mark.parametrize("kind", ["corrupt", "encrypted"])
def test_invalid_pdf(tmp_path: Path, kind: str) -> None:
    pdf = tmp_path / "bad.pdf"
    if kind == "corrupt":
        pdf.write_bytes(b"%PDF-1.7\nbroken")
    else:
        from reportlab.lib.pdfencrypt import StandardEncryption
        from reportlab.pdfgen import canvas

        document = canvas.Canvas(str(pdf), encrypt=StandardEncryption("secret"))
        document.drawString(50, 700, "Protected text")
        document.save()
    assert "Cannot open PDF" in asyncio.run(
        error(
            mcp_server.create_server(tmp_path), "convert_pdf", {"input_pdf": str(pdf)}
        )
    )
    assert not pdf.with_suffix(".md").exists()


def test_custom_assets_and_warning_limit(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = mcp_server.convert

    def convert(paths: Paths, settings: Settings, *, write_report: bool = False):
        report = original(paths, settings, write_report=write_report)
        paths.assets.mkdir()
        (paths.assets / "figure.png").write_bytes(b"image")
        report.warnings = ["first", "first"] + [f"warning {i}" for i in range(21)]
        report.pages[0].warnings = ["first", "last", "last"]
        return report

    monkeypatch.setattr(mcp_server, "convert", convert)
    summary = asyncio.run(
        call(
            mcp_server.create_server(tmp_path),
            "convert_pdf",
            {"input_pdf": str(pdf), "output_markdown": "custom.md"},
        )
    )
    assert summary["assets_directory"] == str(tmp_path / "custom.assets")
    assert summary["warnings_total"] == 23
    assert summary["warnings"] == ["first"] + [f"warning {i}" for i in range(19)]
    assert summary["warnings_omitted"] == 3


def test_unicode_pagination(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = mcp_server.convert
    text = "aé漢🦉\n" * 17

    def convert(paths: Paths, settings: Settings, *, write_report: bool = False):
        report = original(paths, settings, write_report=write_report)
        paths.output.write_text(text, encoding="utf-8")
        return report

    monkeypatch.setattr(mcp_server, "convert", convert)
    server = mcp_server.create_server(tmp_path)
    summary = asyncio.run(call(server, "convert_pdf", {"input_pdf": str(pdf)}))

    async def check() -> None:
        for limit in (4, 5, 7, 9, 16384):
            data = b""
            offset = 0
            while True:
                chunk = await call(
                    server,
                    "read_markdown",
                    {
                        "conversion_id": summary["conversion_id"],
                        "offset_bytes": offset,
                        "max_bytes": limit,
                    },
                )
                encoded = chunk["markdown"].encode()
                assert len(encoded) <= limit
                assert chunk["offset_bytes"] == offset
                assert chunk["next_offset_bytes"] == offset + len(encoded)
                data += encoded
                offset = chunk["next_offset_bytes"]
                if chunk["eof"]:
                    break
                assert encoded
            assert data == text.encode()
        empty = await call(
            server,
            "read_markdown",
            {"conversion_id": summary["conversion_id"], "offset_bytes": len(data)},
        )
        assert empty["markdown"] == ""
        assert empty["eof"]
        for arguments, message in (
            ({"offset_bytes": -1}, "at least 0"),
            ({"offset_bytes": len(data) + 1}, "exceeds"),
            ({"offset_bytes": 2}, "inside a UTF-8"),
            ({"max_bytes": 3}, "between"),
            ({"max_bytes": 16385}, "between"),
        ):
            assert message in await error(
                server,
                "read_markdown",
                {"conversion_id": summary["conversion_id"], **arguments},
            )
        assert "Unknown or expired" in await error(
            server, "read_markdown", {"conversion_id": "/etc/passwd"}
        )

    asyncio.run(check())


@pytest.mark.parametrize("change", ["edit", "replace", "delete", "symlink", "parent"])
def test_read_rejects_changed_result(pdf: Path, tmp_path: Path, change: str) -> None:
    server = mcp_server.create_server(tmp_path)
    summary = asyncio.run(
        call(
            server,
            "convert_pdf",
            {"input_pdf": str(pdf), "output_markdown": "out/a.md"},
        )
    )
    output = Path(summary["output_markdown"])
    if change == "edit":
        output.write_text("changed")
    elif change == "replace":
        data, metadata = output.read_bytes(), output.stat()
        output.unlink()
        output.write_bytes(data)
        os.utime(output, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    elif change == "delete":
        output.unlink()
    elif change == "symlink":
        output.unlink()
        output.symlink_to(pdf)
    else:
        output.parent.rename(tmp_path / "moved")
        output.parent.symlink_to(tmp_path / "moved", target_is_directory=True)
    asyncio.run(
        error(server, "read_markdown", {"conversion_id": summary["conversion_id"]})
    )


def test_root_replacement(pdf: Path, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    nested = make_pdf(root / "input.pdf")
    server = mcp_server.create_server(root)
    summary = asyncio.run(call(server, "convert_pdf", {"input_pdf": str(nested)}))
    root.rename(tmp_path / "old")
    root.mkdir()
    assert "root changed" in asyncio.run(
        error(server, "read_markdown", {"conversion_id": summary["conversion_id"]})
    )


def test_private_registry_and_empty_markdown(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "blank.pdf"
    document = canvas.Canvas(str(pdf))
    document.showPage()
    document.save()
    server = mcp_server.create_server(tmp_path)
    summary = asyncio.run(call(server, "convert_pdf", {"input_pdf": str(pdf)}))
    chunk = asyncio.run(
        call(
            server,
            "read_markdown",
            {"conversion_id": summary["conversion_id"]},
        )
    )
    assert chunk["markdown"] == ""
    assert chunk["next_offset_bytes"] == 0
    assert chunk["eof"]
    assert "Unknown or expired" in asyncio.run(
        error(
            mcp_server.create_server(tmp_path),
            "read_markdown",
            {"conversion_id": summary["conversion_id"]},
        )
    )


@pytest.mark.parametrize("content", [b"bad\xffutf8", b"truncated\xf0\x9f"])
def test_malformed_utf8(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    original = mcp_server.convert

    def convert(paths: Paths, settings: Settings, *, write_report: bool = False):
        report = original(paths, settings, write_report=write_report)
        paths.output.write_bytes(content)
        return report

    monkeypatch.setattr(mcp_server, "convert", convert)
    server = mcp_server.create_server(tmp_path)
    summary = asyncio.run(call(server, "convert_pdf", {"input_pdf": str(pdf)}))
    assert "invalid UTF-8" in asyncio.run(
        error(server, "read_markdown", {"conversion_id": summary["conversion_id"]})
    )


@pytest.mark.parametrize("field", ["input_pdf", "output_markdown"])
@pytest.mark.parametrize("kind", ["relative", "absolute", "prefix", "url", "file-url"])
def test_root_escapes(pdf: Path, tmp_path: Path, field: str, kind: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    make_pdf(root / "paper.pdf")
    values = {
        "relative": "../paper.pdf",
        "absolute": str(pdf),
        "prefix": str(tmp_path / "root-sibling" / "paper.pdf"),
        "url": "https://example.com/paper.pdf",
        "file-url": pdf.as_uri(),
    }
    arguments = {"input_pdf": "paper.pdf", field: values[kind]}
    asyncio.run(error(mcp_server.create_server(root), "convert_pdf", arguments))
    assert sorted(p.name for p in root.iterdir()) == ["paper.pdf"]


@pytest.mark.parametrize(
    "target", ["input", "output", "report", "assets", "work", "parent"]
)
def test_symlink_targets(pdf: Path, tmp_path: Path, target: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    make_pdf(root / "paper.pdf")
    paths = Paths(root / "paper.pdf", root / "out.md")
    arguments = {
        "input_pdf": "paper.pdf",
        "output_markdown": "out.md",
        "overwrite": True,
    }
    if target == "parent":
        (root / "link").symlink_to(tmp_path, target_is_directory=True)
        arguments["output_markdown"] = "link/out.md"
    else:
        destination = {
            "input": paths.input,
            "output": paths.output,
            "report": paths.report,
            "assets": paths.assets,
            "work": paths.work,
        }[target]
        if target == "input":
            destination.unlink()
        destination.symlink_to(pdf if target == "input" else tmp_path / "missing")
    assert "Symlinks" in asyncio.run(
        error(mcp_server.create_server(root), "convert_pdf", arguments)
    )
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("target", ["output", "report", "assets"])
def test_overwrite_guard(pdf: Path, tmp_path: Path, target: str) -> None:
    paths = Paths(pdf, tmp_path / "out.md")
    existing = {"output": paths.output, "report": paths.report, "assets": paths.assets}[
        target
    ]
    if target == "assets":
        existing.mkdir()
    else:
        existing.write_text("previous")
    server = mcp_server.create_server(tmp_path)
    arguments = {"input_pdf": str(pdf), "output_markdown": "out.md"}
    assert "overwrite=True" in asyncio.run(error(server, "convert_pdf", arguments))
    assert not paths.work.exists()
    summary = asyncio.run(call(server, "convert_pdf", {**arguments, "overwrite": True}))
    assert summary["page_count"] == 1


@pytest.mark.parametrize("target", ["output", "report", "assets", "work"])
def test_wrong_target_type(pdf: Path, tmp_path: Path, target: str) -> None:
    paths = Paths(pdf, pdf.with_suffix(".md"))
    destination = {
        "output": paths.output,
        "report": paths.report,
        "assets": paths.assets,
        "work": paths.work,
    }[target]
    if target in {"output", "report"}:
        destination.mkdir()
        (destination / "keep.txt").write_text("keep")
    else:
        destination.write_text("keep")
    asyncio.run(
        error(
            mcp_server.create_server(tmp_path),
            "convert_pdf",
            {"input_pdf": str(pdf), "overwrite": True},
        )
    )
    assert destination.exists()
    if destination.is_dir():
        assert (destination / "keep.txt").read_text() == "keep"
    else:
        assert destination.read_text() == "keep"


@pytest.mark.parametrize(
    "arguments",
    [
        {"source": "invalid"},
        {"engine": "invalid"},
        {"quality": "invalid"},
        {"engine": "vlm", "quality": "fast"},
        {"output_markdown": "nested/out.txt"},
        {"input_pdf": "missing.pdf"},
        {"input_pdf": "folder.pdf"},
        {"input_pdf": "notes.txt"},
    ],
)
def test_invalid_arguments(
    pdf: Path, tmp_path: Path, arguments: dict[str, Any]
) -> None:
    (tmp_path / "folder.pdf").mkdir()
    (tmp_path / "notes.txt").write_text("notes")
    output = tmp_path / "old.md"
    output.write_text("previous")
    asyncio.run(
        error(
            mcp_server.create_server(tmp_path),
            "convert_pdf",
            {
                "input_pdf": str(pdf),
                "output_markdown": "old.md",
                "overwrite": True,
                **arguments,
            },
        )
    )
    assert output.read_text() == "previous"
    assert not (tmp_path / "nested").exists()
    assert not Paths(pdf, output).work.exists()


def test_unrelated_and_unsafe_work(pdf: Path, tmp_path: Path) -> None:
    paths = Paths(pdf, pdf.with_suffix(".md"))
    paths.work.mkdir()
    unrelated = paths.work / "keep.txt"
    unrelated.write_text("keep")
    server = mcp_server.create_server(tmp_path)
    assert "unrelated" in asyncio.run(
        error(server, "convert_pdf", {"input_pdf": str(pdf)})
    )
    assert unrelated.read_text() == "keep"
    (paths.work / "owner").symlink_to(pdf)
    assert "unsafe" in asyncio.run(
        error(server, "convert_pdf", {"input_pdf": str(pdf)})
    )
    assert pdf.read_bytes().startswith(b"%PDF")


def test_publication_failure_and_retry(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(pdf, pdf.with_suffix(".md"))
    paths.output.write_text("previous")
    paths.report.write_text("old report")
    paths.assets.mkdir()
    (paths.assets / "old.png").write_bytes(b"old image")
    replace = Path.replace

    def fail(self: Path, target: Path) -> Path:
        if self.name == "output.md":
            raise OSError("forced publication failure")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", fail)
    server = mcp_server.create_server(tmp_path)
    arguments = {"input_pdf": str(pdf), "overwrite": True}
    assert "forced publication failure" in asyncio.run(
        error(server, "convert_pdf", arguments)
    )
    assert paths.output.read_text() == "previous"
    assert paths.report.read_text() == "old report"
    assert (paths.assets / "old.png").read_bytes() == b"old image"
    monkeypatch.setattr(Path, "replace", replace)
    assert asyncio.run(call(server, "convert_pdf", arguments))["resumed_pages"] == 1


def test_checkpoint_only_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_pdf(tmp_path / "book.pdf", 17)
    original = Inspector.inspect

    def interrupt(
        self: Inspector, numbers: list[int], settings: Settings
    ) -> list[PageInfo]:
        if numbers[0] == 9:
            raise RuntimeError("interrupted")
        return original(self, numbers, settings)

    monkeypatch.setattr(Inspector, "inspect", interrupt)
    server = mcp_server.create_server(tmp_path)
    arguments = {"input_pdf": str(pdf)}
    assert "interrupted" in asyncio.run(error(server, "convert_pdf", arguments))
    paths = Paths(pdf, pdf.with_suffix(".md"))
    assert paths.work.is_dir()
    assert not paths.output.exists()
    monkeypatch.setattr(Inspector, "inspect", original)
    summary = asyncio.run(call(server, "convert_pdf", arguments))
    assert summary["resumed_pages"] == 8
    assert not paths.work.exists()


def test_process_wide_busy_lock(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = Event(), Event()
    original = mcp_server.convert

    def slow(paths: Paths, settings: Settings, *, write_report: bool = False):
        started.set()
        assert release.wait(15)
        return original(paths, settings, write_report=write_report)

    monkeypatch.setattr(mcp_server, "convert", slow)

    async def check() -> None:
        first = asyncio.create_task(
            call(
                mcp_server.create_server(tmp_path),
                "convert_pdf",
                {"input_pdf": str(pdf)},
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 10)
            assert "active" in await error(
                mcp_server.create_server(tmp_path),
                "convert_pdf",
                {"input_pdf": str(pdf), "output_markdown": "second.md"},
            )
            assert not (tmp_path / "second.md").exists()
        finally:
            release.set()
            await first

    asyncio.run(check())


def test_startup_root_and_stdio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(FileNotFoundError):
        mcp_server.create_server(tmp_path / "missing")
    file = tmp_path / "file"
    file.touch()
    with pytest.raises(ValueError, match="directory"):
        mcp_server.create_server(file)
    seen: list[Any] = []

    class Server:
        def run(self, *, transport: str) -> None:
            seen.append(transport)

    def create(root: Path) -> Server:
        seen.append(root)
        return Server()

    monkeypatch.setattr(mcp_server, "create_server", create)
    monkeypatch.delenv("PDF2MD_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="1"):
        mcp_server.main()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PDF2MD_MCP_ROOT must be set" in captured.err
    assert seen == []
    monkeypatch.setenv("PDF2MD_MCP_ROOT", str(tmp_path / "root"))
    mcp_server.main()
    assert seen == [tmp_path / "root", "stdio"]


@pytest.mark.parametrize("root", ["", "missing", "file"])
def test_invalid_startup_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root: str,
) -> None:
    (tmp_path / "file").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PDF2MD_MCP_ROOT", root)
    with pytest.raises(SystemExit, match="1"):
        mcp_server.main()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Error:")
    assert "Traceback" not in captured.err


def test_stdio_protocol_only(pdf: Path, tmp_path: Path) -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "convert_pdf",
                "arguments": {"input_pdf": str(pdf)},
            },
        },
    ]

    # Keep stdin open until conversion completes, as a real MCP client does.
    async def exchange() -> list[dict[str, Any]]:
        process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).parent / "pdf2md-mcp"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PDF2MD_MCP_ROOT": str(tmp_path)},
        )
        try:
            assert process.stdin is not None
            assert process.stdout is not None
            for message in messages:
                process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()
            responses = []
            while True:
                line = await process.stdout.readline()
                assert line, "Server exited before replying."
                response = json.loads(line)
                assert response["jsonrpc"] == "2.0"
                responses.append(response)
                if response.get("id") == 3:
                    summary = response["result"]["structuredContent"]
                    message = {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "read_markdown",
                            "arguments": {
                                "conversion_id": summary["conversion_id"],
                            },
                        },
                    }
                    process.stdin.write((json.dumps(message) + "\n").encode())
                    await process.stdin.drain()
                if response.get("id") == 4:
                    return responses
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()

    responses = asyncio.run(asyncio.wait_for(exchange(), timeout=30))
    by_id = {response["id"]: response for response in responses if "id" in response}
    assert by_id[1]["result"]["serverInfo"]["version"] == __version__
    assert {tool["name"] for tool in by_id[2]["result"]["tools"]} == {
        "convert_pdf",
        "read_markdown",
    }
    assert not by_id[3]["result"].get("isError")
    assert by_id[3]["result"]["structuredContent"]["page_count"] == 1
    assert "Unique page marker" in by_id[4]["result"]["structuredContent"]["markdown"]


@pytest.mark.integration
def test_long_pdf(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "long.pdf", 257)
    server = mcp_server.create_server(tmp_path)
    summary = asyncio.run(call(server, "convert_pdf", {"input_pdf": str(pdf)}))
    assert summary["page_count"] == 257
    report = json.loads(Path(summary["report_json"]).read_text())
    assert len(report["pages"]) == 257
    assert not Paths(pdf, pdf.with_suffix(".md")).work.exists()
    text = pdf.with_suffix(".md").read_text()
    assert text.count("Unique page marker") == 257


@pytest.mark.models
@pytest.mark.parametrize("engine", ["docling", "vlm"])
def test_mcp_apple_models(pdf: Path, tmp_path: Path, engine: str) -> None:
    arguments = {
        "input_pdf": str(pdf),
        "engine": engine,
        "quality": "best" if engine == "vlm" else "fast",
    }
    if engine == "docling":
        import pypdfium2 as pdfium
        from reportlab.pdfgen import canvas

        image = tmp_path / "scan.png"
        with pdfium.PdfDocument(pdf) as document:
            page = document[0]
            bitmap = page.render(scale=2)
            bitmap.to_pil().save(image)
            bitmap.close()
            page.close()
        scan = tmp_path / "scan.pdf"
        document = canvas.Canvas(str(scan), pagesize=(612, 792))
        document.drawImage(str(image), 0, 0, 612, 792)
        document.save()
        arguments.update(input_pdf=str(scan), source="scan")
    summary = asyncio.run(
        call(mcp_server.create_server(tmp_path), "convert_pdf", arguments)
    )
    report = json.loads(Path(summary["report_json"]).read_text())
    assert report["pages"][0]["engine"] == engine
    assert report["pages"][0]["ocr"] == (engine == "docling")
    assert "research" in Path(summary["output_markdown"]).read_text().lower()
