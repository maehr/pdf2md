"""Compare engines in separate processes on local PDFs."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from tempfile import mkdtemp


def evaluate(
    inputs: list[Path],
    output: Path,
    engines: list[str] | None = None,
) -> list[dict[str, object]]:
    for source in inputs:
        if not source.is_file():
            raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    for source in inputs:
        identity = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:8]
        for engine in engines or ["pdf-inspector", "docling", "vlm"]:
            directory = output / f"{source.stem}-{identity}" / engine
            directory.mkdir(parents=True, exist_ok=True)
            directory = Path(mkdtemp(prefix="run-", dir=directory))
            destination = directory / "output.md"
            started = time.perf_counter()
            with (directory / "run.log").open("w") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "pdf2md.cli",
                        str(source.resolve()),
                        "--engine",
                        engine,
                        "--quality",
                        "best",
                        "--report",
                        "-o",
                        str(destination),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                _, status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
            peak_bytes = usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
            result: dict[str, object] = {
                "input": str(source.resolve()),
                "engine": engine,
                "exit_code": process.returncode,
                "wall_seconds": round(time.perf_counter() - started, 3),
                "peak_memory_mib": round(peak_bytes / 1024**2, 1),
                "report": str(destination.with_suffix(".report.json")),
                "log": str(directory / "run.log"),
                "fidelity_review": "pending",
                "platform": platform.platform(),
            }
            results.append(result)
            (output / "results.json").write_text(
                json.dumps(results, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(result), flush=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evaluation"))
    parser.add_argument(
        "--engine", action="append", choices=["pdf-inspector", "docling", "vlm"]
    )
    arguments = parser.parse_args()
    evaluate(arguments.inputs, arguments.output, arguments.engine)
