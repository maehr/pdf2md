"""The original CLI does not require the MCP extra."""

import subprocess
import sys
from pathlib import Path


def test_cli_without_fastmcp(pdf: Path) -> None:
    script = """
import importlib.abc
import sys

class NoMCP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'fastmcp', 'mcp', 'mcp_types'}:
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, NoMCP())
from pdf2md.cli import app
from typer.testing import CliRunner
result = CliRunner().invoke(app, [sys.argv[1], '--engine', 'pdf-inspector'])
assert result.exit_code == 0, result.output
assert not any(name.startswith('fastmcp') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(pdf)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Unique page marker 0001" in pdf.with_suffix(".md").read_text()


def test_missing_extra_error() -> None:
    script = """
import importlib.abc
import sys

class NoMCP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] == 'fastmcp':
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, NoMCP())
from pdf2md.mcp_server import main
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "uv sync --locked --extra mcp" in result.stderr
    assert "Traceback" not in result.stderr
