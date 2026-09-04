"""Fail closed when a built wheel loses required Ocean Partner payloads."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile


REQUIRED_PATHS = (
    "ocean_partner/__init__.py",
    "ocean_partner/cli.py",
    "ocean_partner/resources/skills/core/ocean-analysis-design/SKILL.md",
    "ocean_partner/resources/references/coding/xarray.md",
)


def _latest_wheel(dist_dir: Path) -> Path:
    wheels = sorted(
        dist_dir.glob("oceanmind-*.whl"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not wheels:
        raise SystemExit(f"No oceanmind wheel exists under {dist_dir}")
    return wheels[0]


def _entry_points_path(names: set[str]) -> str | None:
    matches = sorted(name for name in names if name.endswith(".dist-info/entry_points.txt"))
    return matches[0] if len(matches) == 1 else None


def check_wheel(wheel: Path) -> dict[str, object]:
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = [path for path in REQUIRED_PATHS if path not in names]
        entry_points_path = _entry_points_path(names)
        entry_points = archive.read(entry_points_path).decode("utf-8") if entry_points_path else ""

    problems = [f"missing wheel entry: {path}" for path in missing]
    if "ocean = ocean_partner.cli:app" not in entry_points:
        problems.append("missing ocean console entry point")
    if problems:
        raise SystemExit("\n".join(problems))

    return {
        "wheel": str(wheel),
        "required_entries": len(REQUIRED_PATHS),
        "entry_points_path": entry_points_path,
    }


def smoke_wheel_runtime(wheel: Path) -> dict[str, object]:
    """Install the wheel outside the checkout and probe its backend boundary."""

    with tempfile.TemporaryDirectory(prefix="ocean-wheel-smoke-") as directory:
        root = Path(directory)
        target = root / "site-packages"
        install = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--target",
                str(target),
                str(wheel.resolve()),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            raise SystemExit(
                "Could not install the Ocean wheel for its isolated runtime smoke:\n"
                f"{install.stdout}\n{install.stderr}"
            )

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(target)
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import json
from pathlib import Path

import ocean_partner
from ocean_partner.doctor import ocean_doctor

target = Path(__import__("os").environ["PYTHONPATH"]).resolve()
module_path = Path(ocean_partner.__file__).resolve()
report = ocean_doctor()
if not module_path.is_relative_to(target):
    raise RuntimeError(f"Ocean imported outside isolated wheel target: {module_path}")
print(json.dumps({
    "isolated_import": True,
    "desktop_backend": report["backend_schema"],
}, sort_keys=True))
""",
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            raise SystemExit(
                "Ocean wheel isolated runtime smoke failed:\n"
                f"{probe.stdout}\n{probe.stderr}"
            )
        try:
            result = json.loads(probe.stdout)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                "Ocean wheel isolated runtime smoke did not produce JSON:\n"
                f"{probe.stdout}\n{probe.stderr}"
            ) from exc
        if not isinstance(result, dict):
            raise SystemExit("Ocean wheel isolated runtime smoke did not produce an object")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--wheel", type=Path)
    parser.add_argument(
        "--skip-runtime-smoke",
        action="store_true",
        help="Only inspect wheel entries; do not install an isolated runtime probe.",
    )
    args = parser.parse_args()
    wheel = args.wheel or _latest_wheel(args.dist)
    report = check_wheel(wheel)
    if not args.skip_runtime_smoke:
        report["runtime_smoke"] = smoke_wheel_runtime(wheel)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
