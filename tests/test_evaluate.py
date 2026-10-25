import json
import os
import platform
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf2md.models import Paths


def test_repeated_evaluations_use_fresh_directories(
    pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluate = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "evaluate.py")
    )["evaluate"]
    destinations: list[Path] = []

    def start(command: list[str], **kwargs: object) -> SimpleNamespace:
        destination = Path(command[-1])
        work = Paths(pdf, destination).work
        assert not work.exists()
        work.mkdir()
        (work / "checkpoint.json").write_text("completed chunk")
        destinations.append(destination)
        return SimpleNamespace(pid=123, returncode=None)

    monkeypatch.setattr(subprocess, "Popen", start)
    monkeypatch.setattr(
        os, "wait4", lambda *args: (123, 1 << 8, SimpleNamespace(ru_maxrss=1024))
    )
    monkeypatch.setattr(platform, "platform", lambda: "test")
    output = tmp_path / "evaluation"
    first = evaluate([pdf], output, ["pdf-inspector"])
    second = evaluate([pdf], output, ["pdf-inspector"])

    assert first[0]["exit_code"] == second[0]["exit_code"] == 1
    assert destinations[0] != destinations[1]
    for result, destination in zip([first[0], second[0]], destinations, strict=True):
        assert Path(result["log"]).is_file()
        assert result["report"] == str(destination.with_suffix(".report.json"))
        assert Paths(pdf, destination).work.is_dir()
    assert json.loads((output / "results.json").read_text()) == second
