"""Dependency-free child entry for the packaged sandbox isolation self-check."""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Sequence
from pathlib import Path

_PROBE_FILENAME = "sandbox-probe.json"


def run_sandbox_probe(
    *,
    output_directory: Path,
    outside_path: Path,
    required_modules: Sequence[str] = (),
) -> bool:
    """Verify isolation plus imports required by generated scientific code."""

    try:
        outside_path.read_bytes()
    except PermissionError:
        outside_read_denied = True
    else:
        outside_read_denied = False
    scientific_imports: dict[str, str | None] = {}
    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
        except (ImportError, OSError) as exc:  # Native loader diagnostics vary by platform.
            scientific_imports[module_name] = f"{type(exc).__name__}: {exc}"
        else:
            scientific_imports[module_name] = None
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / _PROBE_FILENAME).write_text(
        json.dumps(
            {
                "outside_read_denied": outside_read_denied,
                "scientific_imports": scientific_imports,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return outside_read_denied and all(error is None for error in scientific_imports.values())


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--outside-path", type=Path, required=True)
    parser.add_argument("--required-module", action="append", default=[])
    parsed = parser.parse_args(arguments)
    return 0 if run_sandbox_probe(
        output_directory=parsed.output_directory,
        outside_path=parsed.outside_path,
        required_modules=parsed.required_module,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
