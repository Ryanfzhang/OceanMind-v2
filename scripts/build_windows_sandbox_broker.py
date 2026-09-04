"""Build the native Windows sandbox broker beside a frozen Ocean sidecar.

The broker is deliberately built only on Windows.  Its unsigned development
artifact remains fail closed until release signing, manifest generation, and
native adversarial validation have all completed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    arguments = parser.parse_args()
    if sys.platform != "win32":
        raise RuntimeError("The Windows sandbox broker must be built on native Windows")

    root = Path(__file__).resolve().parents[1]
    manifest = root / "native" / "windows-sandbox-broker" / "Cargo.toml"
    output = arguments.output.resolve()
    work = arguments.work.resolve()
    target = work / "target"
    if not manifest.is_file():
        raise RuntimeError(f"Windows sandbox broker manifest is missing: {manifest}")
    if output.exists():
        output.unlink()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    completed = subprocess.run(
        [
            "cargo",
            "build",
            "--locked",
            "--release",
            "--manifest-path",
            str(manifest),
            "--target-dir",
            str(target),
        ],
        cwd=root,
        check=False,
    )
    if completed.returncode != 0:
        return completed.returncode
    built = target / "release" / "ocean-sandbox-broker.exe"
    if not built.is_file():
        raise RuntimeError(f"Cargo did not create the expected Windows sandbox broker: {built}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
