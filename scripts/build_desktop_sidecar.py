"""Build the platform-local Python sidecar used by the Electron Desktop app.

This deliberately creates a PyInstaller ``onedir`` bundle. Scientific wheels such as
numpy and h5py carry native libraries, so a one-file archive is slower to launch and
harder to inspect/sign. The caller must build it on the target platform.
"""

from __future__ import annotations

import atexit
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from oceanx.scientific_runtime import (
    FROZEN_SCIENTIFIC_RUNTIME_FILENAME,
    frozen_scientific_runtime_manifest,
)

FROZEN_RUNTIME_PROBE_TIMEOUT_SECONDS = 120


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    arguments = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    source = root / "src"
    entry = root / "scripts" / "ocean_desktop_sidecar_entry.py"
    sandbox_probe_entry = source / "oceanx" / "sandbox_probe_entry.py"
    scientific_view_source = source / "oceanx" / "scientific_view.py"
    output = arguments.output.resolve()
    work = arguments.work.resolve()
    dist = work / "dist"
    lock = work.with_name(f"{work.name}.lock")

    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(
            f"Another desktop sidecar build owns {lock}; wait for it before rebuilding"
        ) from exc
    atexit.register(shutil.rmtree, lock, ignore_errors=True)

    if output.exists():
        shutil.rmtree(output)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "ocean-backend",
        "--distpath",
        str(dist),
        "--workpath",
        str(work / "work"),
        "--specpath",
        str(work / "spec"),
        "--paths",
        str(source),
        "--collect-submodules",
        "oceanx",
        "--collect-submodules",
        "deepagents",
        "--collect-submodules",
        "langgraph",
        "--collect-submodules",
        "langchain",
        "--hidden-import",
        "zarr",
        "--hidden-import",
        "fsspec",
        "--hidden-import",
        "numcodecs",
        "--hidden-import",
        "dask",
        "--hidden-import",
        "rasterio",
        "--hidden-import",
        "pyproj",
        "--hidden-import",
        "shapely",
        "--hidden-import",
        "netCDF4",
        "--hidden-import",
        "cartopy",
        "--hidden-import",
        "gsw",
        "--collect-data",
        "oceanx",
        # The sandbox launches the selected scientific Python directly, so this
        # probe must remain a real file rather than only a frozen Python module.
        "--add-data",
        f"{sandbox_probe_entry}:oceanx",
        # Expert programs run under a separately selected scientific Python.
        # Keep the pure-Python builder as a real file so the backend can copy it
        # into each isolated execution root.
        "--add-data",
        f"{scientific_view_source}:oceanx",
        str(entry),
    ]
    completed = subprocess.run(command, cwd=root, check=False)
    if completed.returncode != 0:
        return completed.returncode

    built = dist / "ocean-backend"
    executable = built / ("ocean-backend.exe" if sys.platform == "win32" else "ocean-backend")
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not create the expected sidecar executable: {executable}")
    if sys.platform == "win32":
        broker = built / "ocean-sandbox-broker.exe"
        broker_build = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "build_windows_sandbox_broker.py"),
                "--output",
                str(broker),
                "--work",
                str(work / "windows-sandbox-broker"),
            ],
            cwd=root,
            check=False,
        )
        if broker_build.returncode != 0:
            return broker_build.returncode
        if not broker.is_file():
            raise RuntimeError(f"Windows sandbox broker was not copied beside the sidecar: {broker}")
    _write_frozen_runtime_baseline(executable, built / FROZEN_SCIENTIFIC_RUNTIME_FILENAME)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(built, output)
    print(output)
    return 0


def _write_frozen_runtime_baseline(executable: Path, output: Path) -> None:
    """Ask the final frozen executable for its own dependency identity.

    Capturing after PyInstaller finishes avoids accidentally recording the
    build interpreter instead of the runtime that enters the Electron bundle.
    The temporary sibling plus replace keeps a failed probe from leaving a
    partially trusted baseline behind.
    """

    try:
        completed = subprocess.run(
            [str(executable), "scientific-runtime"],
            cwd=executable.parent,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=FROZEN_RUNTIME_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise RuntimeError("Frozen sidecar could not report its scientific runtime") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "Frozen sidecar scientific-runtime command failed: "
            + (completed.stderr.strip() or str(completed.returncode))
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Frozen sidecar scientific-runtime command emitted invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Frozen sidecar scientific-runtime command emitted an invalid manifest")
    try:
        manifest = frozen_scientific_runtime_manifest(payload)
    except ValueError as exc:
        raise RuntimeError("Frozen sidecar scientific runtime manifest is incompatible") from exc
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(manifest.payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
