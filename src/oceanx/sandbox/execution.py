"""Fail-closed execution primitives for artifact-producing runs.

This module deliberately does not change the permissive shell-tool behavior.
It provides the narrower contract that Ocean analysis runs need: an explicitly
configured sandbox, a scrubbed environment, resource limits, bounded streams,
and process-group cleanup. Backends are macOS Seatbelt, Linux bubblewrap with
seccomp, and the Windows broker. Missing isolation fails closed.
"""

from __future__ import annotations

import asyncio
import errno
import json
import math
import os
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final, cast

try:  # ``resource`` is unavailable on native Windows.
    import resource
except ModuleNotFoundError:  # pragma: no cover - exercised on native Windows only.
    resource = None  # type: ignore[assignment]

from oceanx.sandbox.errors import SandboxUnavailableError
from oceanx.sandbox.platforms import get_platform
from oceanx.sandbox.windows_broker import (
    WindowsBrokerProtocolError,
    build_windows_broker_request,
    parse_windows_broker_result,
    verify_packaged_windows_broker,
)

_MACOS_SYSTEM_PROFILE: Final = Path("/System/Library/Sandbox/Profiles/system.sb")
_SAFE_PATH: Final = "/usr/bin:/bin"
_DEFAULT_CONDA_ENV_NAME: Final = "oceanx"
_CONDA_ENV_NAME_VARIABLE: Final = "OCEAN_CONDA_ENV"
_PYTHON_OVERRIDE_VARIABLE: Final = "OCEAN_SANDBOX_PYTHON"
_RESOURCE_LIMIT_NAMES: Final = (
    "RLIMIT_CPU",
    "RLIMIT_FSIZE",
    "RLIMIT_NPROC",
    "RLIMIT_NOFILE",
)


class SandboxExecutionStatus(str, Enum):
    """Terminal status for one sandboxed command."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RESOURCE_LIMITED = "resource_limited"
    CANCELLED = "cancelled"


class ExecutionTrust(str, Enum):
    """Whether an execution is eligible for a publish gate."""

    SANDBOXED = "sandboxed"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class ResourceLimits:
    """Hard and postflight budgets used by a sandboxed command.

    ``disk_bytes`` maps to ``RLIMIT_FSIZE`` and therefore limits the largest
    single file a process can create.  Aggregate output bytes and file count
    are checked after the process group has stopped; callers must reject those
    results before publishing anything.
    """

    wall_time_seconds: float = 60.0
    cpu_time_seconds: int = 45
    memory_bytes: int = 1_073_741_824
    disk_bytes: int = 268_435_456
    process_count: int = 16
    open_files: int = 128
    stdout_bytes: int = 1_048_576
    stderr_bytes: int = 1_048_576
    output_file_count: int = 128
    output_total_bytes: int = 536_870_912
    memory_poll_interval_seconds: float = 0.05
    termination_grace_seconds: float = 2.0

    def __post_init__(self) -> None:
        numeric_fields = {
            "wall_time_seconds": self.wall_time_seconds,
            "cpu_time_seconds": self.cpu_time_seconds,
            "memory_bytes": self.memory_bytes,
            "disk_bytes": self.disk_bytes,
            "process_count": self.process_count,
            "open_files": self.open_files,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "output_file_count": self.output_file_count,
            "output_total_bytes": self.output_total_bytes,
            "memory_poll_interval_seconds": self.memory_poll_interval_seconds,
            "termination_grace_seconds": self.termination_grace_seconds,
        }
        invalid = [
            name
            for name, value in numeric_fields.items()
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            )
        ]
        if invalid:
            raise ValueError(
                "Sandbox resource limits must be finite positive numbers: "
                + ", ".join(invalid)
            )


WALKING_SKELETON_RESOURCE_LIMITS: Final = ResourceLimits()


@dataclass(frozen=True)
class SandboxExecutionPolicy:
    """Filesystem, environment, and budget contract for a run.

    The caller is responsible for staging code and inputs into explicit roots.
    Nothing from the parent environment is inherited by the child process.
    ``allow_child_processes`` is off for the first Ocean path so a generated
    script cannot daemonize work outside the foreground attempt.
    """

    read_only_roots: tuple[Path | str, ...]
    runtime_read_roots: tuple[Path | str, ...]
    writable_roots: tuple[Path | str, ...]
    output_root: Path | str
    temporary_root: Path | str
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    allow_child_processes: bool = False
    allow_network: bool = False

    def __post_init__(self) -> None:
        read_only_roots = _normalize_existing_paths(self.read_only_roots)
        runtime_read_roots = _normalize_existing_paths(self.runtime_read_roots)
        writable_roots = _normalize_existing_directories(self.writable_roots)
        output_root = _normalize_existing_directory(self.output_root)
        temporary_root = _normalize_existing_directory(self.temporary_root)

        if not writable_roots:
            raise ValueError("Sandbox policy requires at least one writable root")
        if not _is_within_any(output_root, writable_roots):
            raise ValueError("output_root must be inside a writable root")
        if not _is_within_any(temporary_root, writable_roots):
            raise ValueError("temporary_root must be inside a writable root")

        object.__setattr__(self, "read_only_roots", read_only_roots)
        object.__setattr__(self, "runtime_read_roots", runtime_read_roots)
        object.__setattr__(self, "writable_roots", writable_roots)
        object.__setattr__(self, "output_root", output_root)
        object.__setattr__(self, "temporary_root", temporary_root)

    @property
    def readable_roots(self) -> tuple[Path, ...]:
        """Return every root the child may read, in deterministic order."""
        runtime_roots = cast(tuple[Path, ...], self.runtime_read_roots)
        read_only_roots = cast(tuple[Path, ...], self.read_only_roots)
        writable_roots = cast(tuple[Path, ...], self.writable_roots)
        return _unique_paths((*runtime_roots, *read_only_roots, *writable_roots))

    def allows_cwd(self, cwd: Path) -> bool:
        """Return whether ``cwd`` is inside an explicitly mounted root."""
        return _is_within_any(cwd, self.readable_roots)


@dataclass(frozen=True)
class SandboxExecutionCapabilities:
    """Capabilities supplied by the currently selected execution backend."""

    available: bool
    backend: str | None
    command: str | None
    reason: str | None
    hard_limits: tuple[str, ...]
    postflight_limits: tuple[str, ...]


@dataclass(frozen=True)
class PythonSandboxRuntime:
    """One explicitly selected Python environment shared by every sandbox."""

    environment_name: str
    executable: Path
    prefix: Path
    version: str
    read_roots: tuple[Path, ...]
    package_roots: tuple[Path, ...]
    requirements: tuple[str, ...]
    available_modules: tuple[str, ...]


@dataclass(frozen=True)
class OutputTreeSummary:
    """A postflight inventory of files visible in the declared output root."""

    file_count: int
    total_bytes: int
    unsafe_entries: tuple[str, ...]
    limit_exceeded: str | None


@dataclass(frozen=True)
class SandboxExecutionResult:
    """Captured result for one fail-closed sandbox attempt."""

    status: SandboxExecutionStatus
    returncode: int | None
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    trust: ExecutionTrust
    limit_trigger: str | None
    output_summary: OutputTreeSummary
    orphan_processes_terminated: bool = False


@dataclass(frozen=True)
class _CapturedStream:
    data: bytes
    exceeded: bool


def get_sandbox_execution_capabilities() -> SandboxExecutionCapabilities:
    """Report whether this host can run the supported fail-closed backend."""
    platform_name = get_platform()
    if platform_name in {"linux", "wsl"}:
        from oceanx.sandbox.linux import seccomp_filter

        command = shutil.which("bwrap")
        reason = None
        if not command:
            reason = "Linux execution requires bubblewrap (bwrap) and enabled unprivileged user namespaces"
        elif resource is None or any(not hasattr(resource, name) for name in _RESOURCE_LIMIT_NAMES):
            reason = "Required POSIX resource limits are unavailable"
        else:
            try:
                with seccomp_filter(allow_child_processes=False):
                    pass
            except SandboxUnavailableError as exc:
                reason = str(exc)
        return SandboxExecutionCapabilities(
            available=reason is None,
            backend="linux-bubblewrap-seccomp-v1" if reason is None else None,
            command=command,
            reason=reason,
            hard_limits=("wall_time", "cpu_time", "file_size", "process_count",
                         "open_files", "stdout_bytes", "stderr_bytes") if reason is None else (),
            postflight_limits=("memory_rss_monitor", "output_file_count",
                               "output_total_bytes", "output_tree_safety") if reason is None else (),
        )
    if platform_name == "windows":
        broker, reason = verify_packaged_windows_broker()
        if broker is None:
            return SandboxExecutionCapabilities(
                available=False,
                backend=None,
                command=None,
                reason=reason or "Windows sandbox broker is unavailable",
                hard_limits=(),
                postflight_limits=(),
            )
        return SandboxExecutionCapabilities(
            available=True,
            backend="windows-appcontainer-job-v1",
            command=str(broker.executable),
            reason=None,
            hard_limits=(
                "wall_time",
                "cpu_time",
                "memory_bytes",
                "file_size",
                "process_count",
                "open_files",
                "stdout_bytes",
                "stderr_bytes",
                "job_kill_on_close",
            ),
            postflight_limits=(
                "output_file_count",
                "output_total_bytes",
                "output_tree_safety",
            ),
        )
    if platform_name != "macos":
        return SandboxExecutionCapabilities(
            available=False,
            backend=None,
            command=None,
            reason="Ocean sandboxed execution supports macOS, Linux/WSL and the Windows broker",
            hard_limits=(),
            postflight_limits=(),
        )

    command = shutil.which("sandbox-exec")
    if not command:
        return SandboxExecutionCapabilities(
            available=False,
            backend=None,
            command=None,
            reason="macOS sandbox-exec is unavailable",
            hard_limits=(),
            postflight_limits=(),
        )
    if not _MACOS_SYSTEM_PROFILE.is_file():
        return SandboxExecutionCapabilities(
            available=False,
            backend=None,
            command=None,
            reason=f"macOS system sandbox profile is unavailable: {_MACOS_SYSTEM_PROFILE}",
            hard_limits=(),
            postflight_limits=(),
        )
    if resource is None:
        return SandboxExecutionCapabilities(
            available=False,
            backend=None,
            command=None,
            reason="Python resource limits are unavailable on this interpreter",
            hard_limits=(),
            postflight_limits=(),
        )

    missing = tuple(name for name in _RESOURCE_LIMIT_NAMES if not hasattr(resource, name))
    if missing:
        return SandboxExecutionCapabilities(
            available=False,
            backend=None,
            command=None,
            reason=f"Required resource limits are unavailable: {', '.join(missing)}",
            hard_limits=(),
            postflight_limits=(),
        )

    return SandboxExecutionCapabilities(
        available=True,
        backend="macos-seatbelt-rlimit-v1",
        command=command,
        reason=None,
        hard_limits=(
            "wall_time",
            "cpu_time",
            "file_size",
            "process_count",
            "open_files",
            "stdout_bytes",
            "stderr_bytes",
        ),
        postflight_limits=(
            "memory_rss_monitor",
            "output_file_count",
            "output_total_bytes",
            "output_tree_safety",
        ),
    )


def require_sandbox_execution_capabilities() -> SandboxExecutionCapabilities:
    """Return execution capabilities or fail before an unsafe fallback exists."""
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        raise SandboxUnavailableError(capabilities.reason or "sandbox execution is unavailable")
    return capabilities


def _python_launcher(prefix: Path) -> Path:
    return prefix / ("python.exe" if os.name == "nt" else "bin/python")


def _configured_conda_prefixes(environment_name: str) -> tuple[Path, ...]:
    """Find a named Conda environment without activating the backend process."""

    candidates: list[Path] = []
    active_prefix = os.environ.get("CONDA_PREFIX")
    if active_prefix and Path(active_prefix).name == environment_name:
        candidates.append(Path(active_prefix))
    for root in os.environ.get("CONDA_ENVS_PATH", "").split(os.pathsep):
        if root:
            candidates.append(Path(root) / environment_name)
    home = Path.home()
    candidates.extend(
        home / distribution / "envs" / environment_name
        for distribution in ("miniconda3", "anaconda3", "miniforge3", "mambaforge")
    )

    conda = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if conda:
        try:
            completed = subprocess.run(
                (conda, "env", "list", "--json"),
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            payload = json.loads(completed.stdout)
            candidates.extend(
                Path(item)
                for item in payload.get("envs", ())
                if isinstance(item, str) and Path(item).name == environment_name
            )
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
    return tuple(dict.fromkeys(path.expanduser().absolute() for path in candidates))


def _validate_python_launcher(candidate: Path) -> Path:
    launcher = candidate.expanduser().absolute()
    try:
        resolved = launcher.resolve(strict=True)
    except OSError as exc:
        raise SandboxUnavailableError(f"Python interpreter {launcher} is unavailable: {exc}") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise SandboxUnavailableError(f"Python interpreter {launcher} is not executable")
    # Preserve the Conda launcher path. Resolving its symlink would make Python
    # discover the base installation instead of the selected environment.
    return launcher


def current_python_executable() -> Path:
    """Return the Python launcher for the configured ``oceanx`` Conda runtime.

    OceanMind intentionally does not fall back to the backend virtualenv. Set
    ``OCEAN_SANDBOX_PYTHON`` only for an explicit packaged/test override, or
    ``OCEAN_CONDA_ENV`` to select a non-default named environment.
    """

    override = os.environ.get(_PYTHON_OVERRIDE_VARIABLE)
    if override:
        return _validate_python_launcher(Path(override))

    environment_name = os.environ.get(_CONDA_ENV_NAME_VARIABLE, _DEFAULT_CONDA_ENV_NAME).strip()
    if not environment_name:
        environment_name = _DEFAULT_CONDA_ENV_NAME
    for prefix in _configured_conda_prefixes(environment_name):
        launcher = _python_launcher(prefix)
        if launcher.exists():
            return _validate_python_launcher(launcher)
    raise SandboxUnavailableError(
        f"Conda environment '{environment_name}' is unavailable. Create it with "
        f"`conda create -n {environment_name} python=3.11`, then install "
        "OceanMind's root requirements.txt. The backend environment is not used as a fallback."
    )


def current_python_runtime() -> PythonSandboxRuntime:
    """Inspect the selected interpreter and return its immutable sandbox contract."""

    executable = current_python_executable()
    inspection = r'''
import importlib, json, site, sys, sysconfig
from importlib import metadata
checked_modules = (
    "numpy", "pandas", "scipy", "xarray", "matplotlib", "PIL", "h5py",
    "h5netcdf", "zarr", "fsspec", "dask.array", "rasterio", "pyproj",
    "shapely", "netCDF4", "cartopy", "gsw",
)
module_errors = {}
for module_name in checked_modules:
    try:
        importlib.import_module(module_name)
    except (ImportError, OSError) as exc:
        module_errors[module_name] = f"{type(exc).__name__}: {exc}"
packages = sorted({
    f"{dist.metadata['Name']}=={dist.version}"
    for dist in metadata.distributions()
    if dist.metadata.get('Name')
}, key=str.casefold)
print(json.dumps({
    "prefix": sys.prefix,
    "version": platform_version if (platform_version := sys.version.split()[0]) else "unknown",
    "site_packages": site.getsitepackages(),
    "paths": [sysconfig.get_path(key) for key in ("stdlib", "platstdlib", "purelib", "platlib")],
    "requirements": packages,
    "module_errors": module_errors,
    "available_modules": sorted(set(checked_modules) - set(module_errors)),
}))
'''
    try:
        completed = subprocess.run(
            (str(executable), "-c", inspection),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        )
        payload = json.loads(completed.stdout)
        prefix = Path(payload["prefix"]).expanduser().resolve(strict=True)
    except (KeyError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SandboxUnavailableError(
            f"Configured Python runtime {executable} could not be inspected: {exc}"
        ) from exc

    module_errors = payload.get("module_errors")
    required_errors = module_errors if isinstance(module_errors, dict) else {}
    if required_errors:
        details = "; ".join(f"{name}: {error}" for name, error in required_errors.items())
        raise SandboxUnavailableError(
            f"Conda environment '{prefix.name}' is missing required scientific runtime modules. "
            f"Install OceanMind's root requirements.txt. Import failures: {details}"
        )

    raw_package_roots = payload.get("site_packages", ())
    raw_paths = payload.get("paths", ())
    package_roots = _existing_directory_roots(raw_package_roots)
    roots = list(_existing_directory_roots((prefix, executable.parent, *raw_paths, *raw_package_roots)))
    if get_platform() == "macos" and "conda" not in str(prefix).casefold():
        roots.extend(_macos_package_manager_runtime_roots((executable, prefix)))
    requirements = tuple(
        item for item in payload.get("requirements", ()) if isinstance(item, str) and item
    )
    environment_name = os.environ.get(_CONDA_ENV_NAME_VARIABLE, _DEFAULT_CONDA_ENV_NAME)
    if os.environ.get(_PYTHON_OVERRIDE_VARIABLE):
        environment_name = prefix.name
    return PythonSandboxRuntime(
        environment_name=environment_name,
        executable=executable,
        prefix=prefix,
        version=str(payload.get("version") or "unknown"),
        read_roots=_unique_paths(tuple(roots)),
        package_roots=package_roots,
        requirements=requirements,
        available_modules=tuple(
            item for item in payload.get("available_modules", ()) if isinstance(item, str)
        ),
    )


def _existing_directory_roots(values: Sequence[object]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for value in values:
        if not isinstance(value, str | Path) or not value:
            continue
        try:
            resolved = Path(value).expanduser().resolve(strict=True)
        except OSError:
            continue
        roots.append(resolved if resolved.is_dir() else resolved.parent)
    return _unique_paths(tuple(roots))


def current_python_runtime_roots() -> tuple[Path, ...]:
    """Return read-only roots belonging to the configured Conda runtime."""

    return current_python_runtime().read_roots


def _macos_package_manager_runtime_roots(
    locations: Sequence[Path],
) -> tuple[Path, ...]:
    """Return read-only native-library trees belonging to the Python runtime.

    Homebrew records Python under ``<prefix>/Cellar/python...`` while Mach-O
    extensions commonly reference libraries through ``<prefix>/opt/...``.
    Seatbelt does not infer that relationship: both trees must be explicitly
    readable or a normal stdlib import such as ``lzma`` can fail inside the
    sandbox even though it succeeds in the parent process.

    The roots are derived from the selected interpreter locations; this does
    not probe ambient package managers or authorize unrelated filesystem
    paths.  Conda-style runtimes already keep native libraries below
    ``sys.prefix`` and therefore need no additional treatment here.
    """

    roots: list[Path] = []
    for location in locations:
        try:
            resolved = location.expanduser().resolve(strict=True)
        except OSError:
            continue
        for ancestor in (resolved, *resolved.parents):
            if ancestor.name != "Cellar":
                continue
            prefix = ancestor.parent
            for candidate in (
                prefix / "Cellar", prefix / "opt",
                # OpenSSL's public CA bundle lives outside Homebrew's library
                # trees. Permit the bundle, not all of etc (or private keys).
                prefix / "etc" / "openssl@3" / "cert.pem",
                prefix / "etc" / "openssl@3" / "certs",
            ):
                try:
                    candidate_resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if candidate_resolved.is_dir() or candidate_resolved.is_file():
                    roots.append(candidate_resolved)
            break
    return _unique_paths(tuple(roots))


def build_macos_seatbelt_profile(policy: SandboxExecutionPolicy) -> str:
    """Build the restricted Seatbelt profile used by the walking skeleton.

    ``system.sb`` supplies narrowly scoped macOS runtime operations.  The
    profile adds only the declared interpreter, input, and writable roots, and
    permits networking only when requested by the execution service.
    """
    lines = [
        "(version 1)",
        '(import "system.sb")',
        "(allow network*)" if policy.allow_network else "(deny network*)",
        "(allow process-exec)",
    ]
    if policy.allow_child_processes:
        lines.append("(allow process-fork)")
    if policy.allow_network:
        lines.append("(system-network)")

    for root in policy.readable_roots:
        lines.append(_seatbelt_allow_read(root))
        lines.append(_seatbelt_allow_path_ancestors(root))
    for root in cast(tuple[Path, ...], policy.writable_roots):
        lines.append(_seatbelt_allow_write(root))
    return "\n".join(lines)


async def run_sandboxed_command(
    command: Sequence[str],
    *,
    policy: SandboxExecutionPolicy,
    cwd: Path | str,
    environment: Mapping[str, str] | None = None,
) -> SandboxExecutionResult:
    """Run an argv list inside the selected fail-closed sandbox.

    Commands must use an absolute executable path.  This keeps executable
    provenance explicit and prevents an ambient ``PATH`` from selecting a
    different binary than the one whose runtime roots were mounted.
    """
    capabilities = require_sandbox_execution_capabilities()
    resolved_cwd = _normalize_existing_directory(cwd)
    if not policy.allows_cwd(resolved_cwd):
        raise ValueError("cwd must be inside a declared sandbox root")

    argv = _normalize_command(command)
    env = _build_sanitized_environment(policy, environment)
    if capabilities.backend == "windows-appcontainer-job-v1":
        if policy.allow_network:
            raise SandboxUnavailableError("The Windows broker does not support networked execution")
        return await _run_windows_brokered_command(
            capabilities=capabilities,
            command=argv,
            policy=policy,
            cwd=resolved_cwd,
            environment=env,
        )

    _validate_resource_limits(policy.limits)
    started_at = time.monotonic()

    try:
        with ExitStack() as stack:
            descriptors: tuple[int, ...] = ()
            if capabilities.backend == "linux-bubblewrap-seccomp-v1":
                from oceanx.sandbox.linux import build_bubblewrap_command, seccomp_filter

                handle = stack.enter_context(seccomp_filter(
                    allow_child_processes=policy.allow_child_processes,
                    allow_network=policy.allow_network,
                ))
                descriptors = (handle.fileno(),)
                sandbox_argv = build_bubblewrap_command(
                    capabilities.command or "bwrap", argv, policy=policy,
                    cwd=resolved_cwd, seccomp_fd=handle.fileno(),
                )
            else:
                sandbox_argv = (capabilities.command or "sandbox-exec", "-p",
                                build_macos_seatbelt_profile(policy), *argv)
            process = await asyncio.create_subprocess_exec(
                *sandbox_argv,
                cwd=str(resolved_cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
                pass_fds=descriptors,
                preexec_fn=_build_limit_preexec(policy.limits),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxUnavailableError(f"Failed to start sandboxed command: {exc}") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(_capture_stream(process.stdout, policy.limits.stdout_bytes))
    stderr_task = asyncio.create_task(_capture_stream(process.stderr, policy.limits.stderr_bytes))
    process_task = asyncio.create_task(process.wait())
    orphan_processes_terminated = False

    try:
        limit_trigger, stdout, stderr = await _wait_for_completion(
            process_pid=process.pid,
            process_task=process_task,
            stdout_task=stdout_task,
            stderr_task=stderr_task,
            limits=policy.limits,
        )
        if limit_trigger is not None:
            await _terminate_process_group(process, policy.limits.termination_grace_seconds)
            await _finish_stream_tasks(stdout_task, stderr_task)
            stdout = _stream_result(stdout_task)
            stderr = _stream_result(stderr_task)
        elif _process_group_exists(process.pid):
            orphan_processes_terminated = await _terminate_process_group(
                process,
                policy.limits.termination_grace_seconds,
            )
            await _finish_stream_tasks(stdout_task, stderr_task)
            stdout = _stream_result(stdout_task)
            stderr = _stream_result(stderr_task)
    except asyncio.CancelledError:
        await _terminate_process_group(process, policy.limits.termination_grace_seconds)
        await _finish_stream_tasks(stdout_task, stderr_task)
        raise

    duration_seconds = time.monotonic() - started_at
    output_summary = summarize_output_tree(policy.output_root, policy.limits)
    status, classified_trigger = _classify_terminal_status(
        returncode=process.returncode,
        stdout=stdout.data,
        stderr=stderr.data,
        limit_trigger=limit_trigger,
        output_summary=output_summary,
    )
    return SandboxExecutionResult(
        status=status,
        returncode=process.returncode,
        stdout=stdout.data,
        stderr=stderr.data,
        duration_seconds=duration_seconds,
        trust=ExecutionTrust.SANDBOXED,
        limit_trigger=classified_trigger,
        output_summary=output_summary,
        orphan_processes_terminated=orphan_processes_terminated,
    )


async def _run_windows_brokered_command(
    *,
    capabilities: SandboxExecutionCapabilities,
    command: Sequence[str],
    policy: SandboxExecutionPolicy,
    cwd: Path,
    environment: Mapping[str, str],
) -> SandboxExecutionResult:
    """Delegate one policy-owned run to the signed AppContainer broker.

    The Python sidecar does not start the generated code directly on Windows.
    It gives the broker an inherited JSON stream, then treats a missing,
    malformed, or incompletely cleaned Job Object as a failed sandbox result.
    """

    broker = capabilities.command
    if not broker:
        raise SandboxUnavailableError("Windows sandbox broker command is unavailable")
    try:
        request = build_windows_broker_request(
            command,
            policy=policy,
            cwd=cwd,
            environment=environment,
        )
    except WindowsBrokerProtocolError as exc:
        raise SandboxUnavailableError(f"Windows sandbox broker request is invalid: {exc}") from exc
    started_at = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            broker,
            "run",
            "--stdio",
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={},
        )
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(request),
            timeout=policy.limits.wall_time_seconds + policy.limits.termination_grace_seconds + 5.0,
        )
    except TimeoutError:
        if process.returncode is None:
            process.kill()
            await process.wait()
        summary = summarize_output_tree(policy.output_root, policy.limits)
        return SandboxExecutionResult(
            status=SandboxExecutionStatus.TIMED_OUT,
            returncode=process.returncode,
            stdout=b"",
            stderr=b"",
            duration_seconds=time.monotonic() - started_at,
            trust=ExecutionTrust.SANDBOXED,
            limit_trigger="broker_wall_time",
            output_summary=summary,
            orphan_processes_terminated=False,
        )
    except OSError as exc:
        raise SandboxUnavailableError(f"Failed to start Windows sandbox broker: {exc}") from exc
    if process.returncode != 0:
        raise SandboxUnavailableError("Windows sandbox broker exited without a trusted result")
    try:
        result = parse_windows_broker_result(stdout)
    except WindowsBrokerProtocolError as exc:
        raise SandboxUnavailableError(f"Windows sandbox broker returned an invalid result: {exc}") from exc
    if not result.job_terminated:
        raise SandboxUnavailableError("Windows sandbox broker did not prove Job Object cleanup")
    summary = summarize_output_tree(policy.output_root, policy.limits)
    status = SandboxExecutionStatus(result.status)
    limit_trigger = result.limit_trigger
    if summary.limit_exceeded is not None:
        status = SandboxExecutionStatus.RESOURCE_LIMITED
        limit_trigger = summary.limit_exceeded
    elif summary.unsafe_entries:
        status = SandboxExecutionStatus.FAILED
        limit_trigger = "unsafe_output_tree"
    return SandboxExecutionResult(
        status=status,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_seconds=result.duration_seconds,
        trust=ExecutionTrust.SANDBOXED,
        limit_trigger=limit_trigger,
        output_summary=summary,
        orphan_processes_terminated=result.job_terminated,
    )


def summarize_output_tree(output_root: Path | str, limits: ResourceLimits) -> OutputTreeSummary:
    """Inventory regular output files without following untrusted links."""
    root = _normalize_existing_directory(output_root)
    file_count = 0
    total_bytes = 0
    unsafe_entries: list[str] = []

    for path in _walk_output_tree(root):
        relative = path.relative_to(root).as_posix()
        stat_result = path.lstat()
        if path.is_symlink():
            unsafe_entries.append(f"symlink:{relative}")
            continue
        if _is_reparse_point(path):
            unsafe_entries.append(f"reparse_point:{relative}")
            continue
        if _has_alternate_data_stream_name(relative):
            unsafe_entries.append(f"alternate_data_stream:{relative}")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            unsafe_entries.append(f"non_regular:{relative}")
            continue
        if stat_result.st_nlink != 1:
            unsafe_entries.append(f"hardlink:{relative}")
            continue
        file_count += 1
        total_bytes += stat_result.st_size

    limit_exceeded = None
    if file_count > limits.output_file_count:
        limit_exceeded = "output_file_count"
    elif total_bytes > limits.output_total_bytes:
        limit_exceeded = "output_total_bytes"
    return OutputTreeSummary(
        file_count=file_count,
        total_bytes=total_bytes,
        unsafe_entries=tuple(unsafe_entries),
        limit_exceeded=limit_exceeded,
    )


async def _wait_for_completion(
    *,
    process_pid: int | None,
    process_task: asyncio.Task[int],
    stdout_task: asyncio.Task[_CapturedStream],
    stderr_task: asyncio.Task[_CapturedStream],
    limits: ResourceLimits,
) -> tuple[str | None, _CapturedStream, _CapturedStream]:
    """Wait for process completion, a bounded stream, or the wall deadline."""
    active: set[asyncio.Task[object]] = {process_task, stdout_task, stderr_task}
    deadline = time.monotonic() + limits.wall_time_seconds
    next_memory_sample = time.monotonic() + limits.memory_poll_interval_seconds
    stdout: _CapturedStream | None = None
    stderr: _CapturedStream | None = None

    while active:
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            return "wall_time", _stream_result(stdout_task), _stream_result(stderr_task)
        remaining = min(remaining, max(0.0, next_memory_sample - now))
        done, _ = await asyncio.wait(
            active,
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            if time.monotonic() >= deadline:
                return "wall_time", _stream_result(stdout_task), _stream_result(stderr_task)
            rss_bytes = await _read_process_rss_bytes(process_pid)
            next_memory_sample = time.monotonic() + limits.memory_poll_interval_seconds
            if rss_bytes is not None and rss_bytes > limits.memory_bytes:
                return "memory_bytes", _stream_result(stdout_task), _stream_result(stderr_task)
            continue
        active.difference_update(done)

        for task in done:
            if task is stdout_task:
                stdout = stdout_task.result()
                if stdout.exceeded:
                    return "stdout_bytes", stdout, _stream_result(stderr_task)
            elif task is stderr_task:
                stderr = stderr_task.result()
                if stderr.exceeded:
                    return "stderr_bytes", _stream_result(stdout_task), stderr

        if process_task.done() and stdout_task.done() and stderr_task.done():
            return None, stdout or stdout_task.result(), stderr or stderr_task.result()

    return None, stdout or stdout_task.result(), stderr or stderr_task.result()


async def _capture_stream(stream: asyncio.StreamReader, limit: int) -> _CapturedStream:
    captured = bytearray()
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            return _CapturedStream(data=bytes(captured), exceeded=False)
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            return _CapturedStream(data=bytes(captured), exceeded=True)


async def _read_process_rss_bytes(pid: int | None) -> int | None:
    """Read one macOS RSS sample for the foreground sandbox process.

    macOS exposes ``RLIMIT_AS`` but does not reliably let unprivileged callers
    lower it on modern hosts.  The v1 policy therefore samples the sole
    foreground process (child processes are disabled by default) and kills the
    process group once it crosses the configured memory ceiling.
    """
    if pid is None:
        return None
    if get_platform() in {"linux", "wsl"}:
        # The foreground PID is bubblewrap, not Python. Account for the whole
        # group, including its namespace init and scientific process/children.
        total = 0
        page_size = os.sysconf("SC_PAGE_SIZE")
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                # comm (field 2) can contain spaces/parentheses; parse after its
                # final closing parenthesis. pgrp is field 5, RSS is field 24.
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == pid:
                    total += int(fields[21]) * page_size
            except (OSError, ValueError, IndexError):
                continue  # A process may exit between directory listing and read.
        return total
    process = await asyncio.create_subprocess_exec(
        "/bin/ps",
        "-o",
        "rss=",
        "-p",
        str(pid),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": _SAFE_PATH},
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return None
    value = stdout.decode("ascii", errors="ignore").strip()
    try:
        return int(value) * 1024
    except ValueError:
        return None


async def _finish_stream_tasks(
    stdout_task: asyncio.Task[_CapturedStream],
    stderr_task: asyncio.Task[_CapturedStream],
) -> None:
    pending = [task for task in (stdout_task, stderr_task) if not task.done()]
    if not pending:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=1.0)
    except TimeoutError:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def _stream_result(task: asyncio.Task[_CapturedStream]) -> _CapturedStream:
    if task.done() and not task.cancelled():
        try:
            return task.result()
        except Exception:  # noqa: BLE001 - cancelled pipe readers fail heterogeneously
            return _CapturedStream(data=b"", exceeded=False)
    return _CapturedStream(data=b"", exceeded=False)


async def _terminate_process_group(
    process: asyncio.subprocess.Process,
    grace_seconds: float,
) -> bool:
    """Terminate the process group, then hard-kill leftovers after grace."""
    pid = process.pid
    if pid is None or not _process_group_exists(pid):
        return False

    _signal_process_group(pid, signal.SIGTERM)
    await _wait_for_process_group_exit(pid, grace_seconds)
    if _process_group_exists(pid):
        _signal_process_group(pid, signal.SIGKILL)
        await _wait_for_process_group_exit(pid, grace_seconds)
    if process.returncode is None:
        await process.wait()
    return True


async def _wait_for_process_group_exit(pid: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while _process_group_exists(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)


def _process_group_exists(pid: int | None) -> bool:
    if pid is None or os.name != "posix":
        return False
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_process_group(pid: int, signal_number: signal.Signals) -> None:
    try:
        os.killpg(pid, signal_number)
    except ProcessLookupError:
        return
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise


def _classify_terminal_status(
    *,
    returncode: int | None,
    stdout: bytes,
    stderr: bytes,
    limit_trigger: str | None,
    output_summary: OutputTreeSummary,
) -> tuple[SandboxExecutionStatus, str | None]:
    if limit_trigger == "wall_time":
        return SandboxExecutionStatus.TIMED_OUT, limit_trigger
    if limit_trigger is not None:
        return SandboxExecutionStatus.RESOURCE_LIMITED, limit_trigger
    if output_summary.limit_exceeded is not None:
        return SandboxExecutionStatus.RESOURCE_LIMITED, output_summary.limit_exceeded
    if output_summary.unsafe_entries:
        return SandboxExecutionStatus.FAILED, "unsafe_output_tree"
    if returncode == 0:
        return SandboxExecutionStatus.SUCCEEDED, None

    combined = (stdout + b"\n" + stderr).lower()
    if b"memoryerror" in combined or b"cannot allocate memory" in combined:
        return SandboxExecutionStatus.RESOURCE_LIMITED, "memory_bytes"
    if b"file too large" in combined:
        return SandboxExecutionStatus.RESOURCE_LIMITED, "disk_bytes"
    if returncode is not None and returncode < 0:
        signal_number = -returncode
        if signal_number == signal.SIGXCPU:
            return SandboxExecutionStatus.RESOURCE_LIMITED, "cpu_time_seconds"
        if signal_number == signal.SIGXFSZ:
            return SandboxExecutionStatus.RESOURCE_LIMITED, "disk_bytes"
    return SandboxExecutionStatus.FAILED, None


def _build_sanitized_environment(
    policy: SandboxExecutionPolicy,
    environment: Mapping[str, str] | None,
) -> dict[str, str]:
    temporary_root = cast(Path, policy.temporary_root)
    home = temporary_root / "home"
    cache = temporary_root / "cache"
    matplotlib = temporary_root / "matplotlib"
    for directory in (home, cache, matplotlib):
        directory.mkdir(parents=True, exist_ok=True)

    sanitized = {
        "MPLCONFIGDIR": str(matplotlib),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if policy.allow_network:
        # Homebrew cert.pem is a symlink outside its library trees. The policy
        # mounts the canonical public bundle; OpenSSL must use that exact path.
        for root in policy.runtime_read_roots:
            if Path(root).name == "cert.pem" and Path(root).is_file():
                sanitized["SSL_CERT_FILE"] = str(root)
                break
    if get_platform() in {"linux", "wsl"}:
        # Linux RLIMIT_NPROC includes threads. Do not let BLAS eagerly allocate
        # one worker per server CPU before the scientific program even starts.
        sanitized.update({
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "MPLBACKEND": "Agg",
        })
    if get_platform() == "windows":
        # The native broker creates AppContainer-local HOME/TEMP variables and
        # never accepts the parent process PATH or user-profile variables.
        sanitized["TEMP"] = str(temporary_root)
        sanitized["TMP"] = str(temporary_root)
    else:
        sanitized.update(
            {
                "HOME": str(home),
                "TMPDIR": str(temporary_root),
                "XDG_CACHE_HOME": str(cache),
                "PATH": _SAFE_PATH,
            }
        )
    if environment is not None:
        sanitized.update({key: value for key, value in environment.items()})
    return sanitized


def _validate_resource_limits(limits: ResourceLimits) -> None:
    if resource is None:
        raise SandboxUnavailableError("Python resource limits are unavailable on this interpreter")

    requested = (
        (resource.RLIMIT_CPU, limits.cpu_time_seconds),
        (resource.RLIMIT_FSIZE, limits.disk_bytes),
        (resource.RLIMIT_NPROC, limits.process_count),
        (resource.RLIMIT_NOFILE, limits.open_files),
    )
    for resource_id, value in requested:
        _, hard = resource.getrlimit(resource_id)
        if hard != resource.RLIM_INFINITY and hard < value:
            raise SandboxUnavailableError(
                f"Host hard resource limit {resource_id} is lower than required policy value {value}"
            )


def _build_limit_preexec(limits: ResourceLimits) -> Callable[[], None]:
    if resource is None:  # Defensive: capabilities were checked before spawn.
        raise SandboxUnavailableError("Python resource limits are unavailable on this interpreter")

    resource_limits = (
        (resource.RLIMIT_CPU, limits.cpu_time_seconds),
        (resource.RLIMIT_FSIZE, limits.disk_bytes),
        (resource.RLIMIT_NPROC, limits.process_count),
        (resource.RLIMIT_NOFILE, limits.open_files),
    )

    def apply_limits() -> None:
        for resource_id, value in resource_limits:
            resource.setrlimit(resource_id, (value, value))

    return apply_limits


def _normalize_command(command: Sequence[str]) -> tuple[str, ...]:
    if not command:
        raise ValueError("Sandbox command must not be empty")
    executable = Path(command[0]).expanduser()
    if not executable.is_absolute():
        raise ValueError("Sandbox command executable must be an absolute path")
    try:
        executable.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Sandbox command executable does not exist: {executable}") from exc
    # Keep the requested absolute path for exec.  Resolving a virtualenv's
    # ``bin/python`` symlink before exec makes CPython discover the base prefix
    # instead of the intended environment and silently drops scientific extras.
    # The resolved target is still covered by ``runtime_read_roots``.
    return (str(executable), *map(str, command[1:]))


def _normalize_existing_paths(paths: Sequence[Path | str]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for path in paths:
        candidate = Path(path).expanduser()
        _reject_link_like_root(candidate)
        try:
            normalized.append(candidate.resolve(strict=True))
        except OSError as exc:
            raise ValueError(f"Sandbox path does not exist: {candidate}") from exc
    return _unique_paths(tuple(normalized))


def _normalize_existing_directories(paths: Sequence[Path | str]) -> tuple[Path, ...]:
    return tuple(_normalize_existing_directory(path) for path in paths)


def _normalize_existing_directory(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    _reject_link_like_root(candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Sandbox directory does not exist: {candidate}") from exc
    if not resolved.is_dir():
        raise ValueError(f"Sandbox path must be a directory: {resolved}")
    return resolved


def _reject_link_like_root(candidate: Path) -> None:
    """Reject a declared root that would silently resolve to another object.

    Runtime, input, writable, output, and temporary roots are policy authority,
    not untrusted in-tree artifacts.  Accepting a direct symlink or Windows
    reparse point here would make the Seatbelt profile describe a different
    location from the one the caller reviewed.  Descendant links remain visible
    to the OS policy and output-tree verifier, where they are rejected too.
    """

    try:
        if _is_reparse_point(candidate):
            raise ValueError(f"Sandbox root must not be a symlink or reparse point: {candidate}")
    except OSError as exc:
        raise ValueError(f"Sandbox path does not exist: {candidate}") from exc


def _unique_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(sorted(set(paths), key=str))


def _is_within_any(path: Path, roots: Sequence[Path]) -> bool:
    return any(_is_within(path, root) for root in roots)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _seatbelt_allow_read(path: Path) -> str:
    return f"(allow file-read* {_seatbelt_path_filter(path)})"


def _seatbelt_allow_write(path: Path) -> str:
    return f"(allow file-write* {_seatbelt_path_filter(path)})"


def _seatbelt_allow_path_ancestors(path: Path) -> str:
    ancestor_target = path if path.is_dir() else path.parent
    return (
        "(allow file-read-metadata file-test-existence "
        f"(path-ancestors {_seatbelt_string(ancestor_target)}))"
    )


def _seatbelt_path_filter(path: Path) -> str:
    if path.is_dir():
        return f"(subpath {_seatbelt_string(path)})"
    return f"(literal {_seatbelt_string(path)})"


def _seatbelt_string(path: Path) -> str:
    # ``sandbox-exec -p`` consumes the profile as UTF-8 Scheme source.  JSON's
    # default ASCII escaping turns a path such as ``测试`` into ``\u6d4b...``;
    # Seatbelt does not decode that JSON-only escape form, so the declared root
    # no longer matches the real filesystem path and even the staged runner is
    # denied with EPERM.  Keep JSON's safe quote/backslash escaping while
    # emitting the actual Unicode code points expected by Seatbelt.
    return json.dumps(str(path), ensure_ascii=False)


def _walk_output_tree(root: Path) -> list[Path]:
    paths: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                paths.append(path)
                if not _is_reparse_point(path) and entry.is_dir(follow_symlinks=False):
                    stack.append(path)
    return paths


def _is_reparse_point(path: Path) -> bool:
    """Return whether a path is a link-like filesystem object on this platform."""

    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _has_alternate_data_stream_name(relative: str) -> bool:
    """Reject a portable output name that would address an NTFS alternate stream."""

    return any(":" in component for component in Path(relative).parts)
