"""Small, model-free proof that the current Ocean runtime can enforce Seatbelt.

The desktop packager invokes this through the frozen sidecar. It is deliberately
separate from ``doctor``: capability discovery is useful to users, while release
validation needs to execute one constrained child process before it can claim
that Expert-owned scientific code execution is available.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from oceanx.sandbox import (
    ResourceLimits,
    SandboxExecutionPolicy,
    SandboxExecutionStatus,
    SandboxUnavailableError,
    current_python_executable,
    current_python_runtime_roots,
    get_sandbox_execution_capabilities,
    run_sandboxed_command,
)
from oceanx.sandbox_probe_entry import run_sandbox_probe

_PROBE_FILENAME = "sandbox-probe.json"
_REQUIRED_SCIENTIFIC_MODULES = ("zarr", "netCDF4", "gsw")


def _probe_command() -> tuple[str, ...]:
    executable = str(current_python_executable())
    return executable, str(Path(__file__).resolve().with_name("sandbox_probe_entry.py"))


def _probe_environment() -> dict[str, str]:
    """Never inject the backend virtualenv into the selected Conda runtime."""

    return {"PYTHONNOUSERSITE": "1"}


def _probe_runtime_roots() -> tuple[Path, ...]:
    roots = [*current_python_runtime_roots(), Path(__file__).resolve().parents[1]]
    return tuple(dict.fromkeys(path.resolve() for path in roots if path.exists()))


async def run_sandbox_self_check() -> dict[str, Any]:
    """Execute the minimal read/write isolation contract and return safe JSON."""

    capabilities = get_sandbox_execution_capabilities()
    report: dict[str, Any] = {
        "schema_version": "ocean-sandbox-self-check/v1",
        "backend": capabilities.backend,
        "available": capabilities.available,
        "passed": False,
        "checks": {
            "declared_output_written": False,
            "outside_read_denied": False,
            "scientific_imports": {
                module_name: False for module_name in _REQUIRED_SCIENTIFIC_MODULES
            },
        },
    }
    if not capabilities.available:
        report["reason"] = capabilities.reason
        return report

    with tempfile.TemporaryDirectory(prefix="ocean-sandbox-self-check-") as temporary_directory:
        root = Path(temporary_directory)
        work = root / "work"
        output = root / "output"
        temporary = root / "temporary"
        private = root / "private.txt"
        for directory in (work, output, temporary):
            directory.mkdir()
        private.write_text("must remain unreadable", encoding="utf-8")
        policy = SandboxExecutionPolicy(
            read_only_roots=(work,),
            runtime_read_roots=_probe_runtime_roots(),
            writable_roots=(output, temporary),
            output_root=output,
            temporary_root=temporary,
            limits=ResourceLimits(
                wall_time_seconds=10.0,
                cpu_time_seconds=8,
                memory_bytes=268_435_456,
                disk_bytes=65_536,
                process_count=4,
                open_files=64,
                stdout_bytes=4_096,
                stderr_bytes=4_096,
                output_file_count=4,
                output_total_bytes=65_536,
                termination_grace_seconds=0.5,
            ),
        )
        try:
            result = await run_sandboxed_command(
                (
                    *_probe_command(),
                    "--output-directory",
                    str(output),
                    "--outside-path",
                    str(private),
                    *(
                        argument
                        for module_name in _REQUIRED_SCIENTIFIC_MODULES
                        for argument in ("--required-module", module_name)
                    ),
                ),
                policy=policy,
                cwd=work,
                environment=_probe_environment(),
            )
        except SandboxUnavailableError as exc:
            report["reason"] = str(exc)
            return report

        report["result"] = {
            "status": result.status.value,
            "limit_trigger": result.limit_trigger,
            "returncode": result.returncode,
        }
        if result.status is not SandboxExecutionStatus.SUCCEEDED:
            diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
            report["reason"] = diagnostic[-2_000:] or (
                f"Sandbox probe exited with code {result.returncode}"
            )
        probe_path = output / _PROBE_FILENAME
        try:
            probe = json.loads(probe_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            probe = {}
        output_written = result.output_summary.file_count == 1 and result.output_summary.unsafe_entries == ()
        outside_read_denied = probe.get("outside_read_denied") is True
        import_results = probe.get("scientific_imports")
        scientific_imports = {
            module_name: isinstance(import_results, dict)
            and import_results.get(module_name) is None
            for module_name in _REQUIRED_SCIENTIFIC_MODULES
        }
        report["checks"] = {
            "declared_output_written": output_written,
            "outside_read_denied": outside_read_denied,
            "scientific_imports": scientific_imports,
        }
        report["passed"] = (
            result.status is SandboxExecutionStatus.SUCCEEDED
            and output_written
            and outside_read_denied
            and all(scientific_imports.values())
        )
    return report


__all__ = ["run_sandbox_probe", "run_sandbox_self_check"]
