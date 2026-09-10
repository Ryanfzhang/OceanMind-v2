"""Tests for the fail-closed artifact-run sandbox contract."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from oceanx.sandbox import (
    ExecutionTrust,
    OutputTreeSummary,
    ResourceLimits,
    SandboxExecutionPolicy,
    SandboxExecutionResult,
    SandboxExecutionStatus,
    SandboxUnavailableError,
    build_macos_seatbelt_profile,
    current_python_executable,
    current_python_runtime_roots,
    get_sandbox_execution_capabilities,
    run_sandboxed_command,
    summarize_output_tree,
)
from oceanx.sandbox.execution import _macos_package_manager_runtime_roots
from oceanx.sandbox.windows_broker import WindowsBrokerInstallation


def _resource_limits(**updates: int | float) -> ResourceLimits:
    values: dict[str, int | float] = {
        "wall_time_seconds": 5.0,
        "cpu_time_seconds": 4,
        "memory_bytes": 536_870_912,
        "disk_bytes": 1_048_576,
        "process_count": 4,
        "open_files": 64,
        "stdout_bytes": 4_096,
        "stderr_bytes": 4_096,
        "output_file_count": 8,
        "output_total_bytes": 8_192,
        "termination_grace_seconds": 0.2,
    }
    values.update(updates)
    return ResourceLimits(**values)


@pytest.mark.parametrize(
    "field",
    (
        "wall_time_seconds",
        "cpu_time_seconds",
        "memory_bytes",
        "disk_bytes",
        "process_count",
        "open_files",
        "stdout_bytes",
        "stderr_bytes",
        "output_file_count",
        "output_total_bytes",
        "memory_poll_interval_seconds",
        "termination_grace_seconds",
    ),
)
@pytest.mark.parametrize("value", (float("nan"), float("inf"), True))
def test_resource_limits_reject_nonfinite_or_boolean_budgets(field: str, value: int | float | bool):
    with pytest.raises(ValueError, match="finite positive"):
        _resource_limits(**{field: value})


def _python_command(script: Path, *arguments: str) -> tuple[str, ...]:
    return str(current_python_executable()), str(script), *arguments


def _policy(
    tmp_path: Path, *, limits: ResourceLimits | None = None
) -> tuple[SandboxExecutionPolicy, Path]:
    work = tmp_path / "work"
    outputs = tmp_path / "outputs"
    temporary = tmp_path / "temporary"
    for directory in (work, outputs, temporary):
        directory.mkdir()
    policy = SandboxExecutionPolicy(
        read_only_roots=(work,),
        runtime_read_roots=current_python_runtime_roots(),
        writable_roots=(outputs, temporary),
        output_root=outputs,
        temporary_root=temporary,
        limits=limits or _resource_limits(),
    )
    return policy, work


def test_policy_requires_output_and_temporary_roots_to_be_writable(tmp_path: Path):
    work = tmp_path / "work"
    output = tmp_path / "output"
    temporary = tmp_path / "temporary"
    for directory in (work, output, temporary):
        directory.mkdir()

    with pytest.raises(ValueError, match="output_root"):
        SandboxExecutionPolicy(
            read_only_roots=(work,),
            runtime_read_roots=(),
            writable_roots=(temporary,),
            output_root=output,
            temporary_root=temporary,
        )


def test_policy_rejects_a_linked_declared_root(tmp_path: Path):
    work = tmp_path / "work"
    output = tmp_path / "output"
    temporary = tmp_path / "temporary"
    linked_work = tmp_path / "linked-work"
    for directory in (work, output, temporary):
        directory.mkdir()
    linked_work.symlink_to(work, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink or reparse point"):
        SandboxExecutionPolicy(
            read_only_roots=(linked_work,),
            runtime_read_roots=(),
            writable_roots=(output, temporary),
            output_root=output,
            temporary_root=temporary,
        )


def test_seatbelt_profile_has_no_network_and_only_declared_write_roots(tmp_path: Path):
    policy, work = _policy(tmp_path)

    profile = build_macos_seatbelt_profile(policy)

    assert '(import "system.sb")' in profile
    assert "(deny network*)" in profile
    assert "(allow process-fork)" not in profile
    assert f'(allow file-read* (subpath "{work.resolve()}"))' in profile
    assert f'(allow file-write* (subpath "{policy.output_root}"))' in profile


def test_seatbelt_profile_preserves_unicode_paths(tmp_path: Path) -> None:
    task_root = tmp_path / "OceanMind Tasks" / "测试分析"
    work = task_root / "work"
    output = task_root / "output"
    temporary = task_root / "temporary"
    for directory in (work, output, temporary):
        directory.mkdir(parents=True)
    policy = SandboxExecutionPolicy(
        read_only_roots=(work,),
        runtime_read_roots=(),
        writable_roots=(output, temporary),
        output_root=output,
        temporary_root=temporary,
    )

    profile = build_macos_seatbelt_profile(policy)

    assert "测试分析" in profile
    assert "\\u6d4b" not in profile


@pytest.mark.asyncio
async def test_sandbox_can_open_a_runner_below_a_unicode_task_root(tmp_path: Path) -> None:
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    task_root = tmp_path / "OceanMind Tasks" / "测试分析"
    work = task_root / "work"
    output = task_root / "output"
    temporary = task_root / "temporary"
    for directory in (work, output, temporary):
        directory.mkdir(parents=True)
    runner = work / "_oceanmind_runner.py"
    runner.write_text("print('unicode runner opened')\n", encoding="utf-8")
    policy = SandboxExecutionPolicy(
        read_only_roots=(work,),
        runtime_read_roots=current_python_runtime_roots(),
        writable_roots=(output, temporary),
        output_root=output,
        temporary_root=temporary,
        limits=_resource_limits(),
    )

    result = await run_sandboxed_command(
        _python_command(runner),
        policy=policy,
        cwd=work,
    )

    assert result.status is SandboxExecutionStatus.SUCCEEDED, result.stderr.decode(
        "utf-8", errors="replace"
    )
    assert result.stdout == b"unicode runner opened\n"


def test_output_summary_rejects_links_and_enforces_aggregate_budget(tmp_path: Path):
    root = tmp_path / "outputs"
    root.mkdir()
    (root / "one.bin").write_bytes(b"1234")
    (root / "two.bin").write_bytes(b"5678")
    (root / "linked.bin").symlink_to(root / "one.bin")

    summary = summarize_output_tree(
        root,
        _resource_limits(output_file_count=1, output_total_bytes=5),
    )

    assert summary.file_count == 2
    assert summary.total_bytes == 8
    assert summary.limit_exceeded == "output_file_count"
    assert summary.unsafe_entries == ("symlink:linked.bin",)


def test_output_summary_rejects_windows_style_alternate_data_stream_name(tmp_path: Path):
    root = tmp_path / "outputs"
    root.mkdir()
    (root / "result.nc:metadata").write_bytes(b"unsafe")

    summary = summarize_output_tree(root, _resource_limits())

    assert summary.unsafe_entries == ("alternate_data_stream:result.nc:metadata",)


def test_output_summary_marks_a_reparse_directory_without_descending_into_it(tmp_path: Path, monkeypatch):
    root = tmp_path / "outputs"
    junction = root / "junction"
    root.mkdir()
    junction.mkdir()
    (junction / "escaped.nc").write_bytes(b"unsafe")

    monkeypatch.setattr(
        "oceanx.sandbox.execution._is_reparse_point",
        lambda path: path.name == "junction",
    )
    summary = summarize_output_tree(root, _resource_limits())

    assert summary.file_count == 0
    assert summary.unsafe_entries == ("reparse_point:junction",)


def test_execution_capabilities_fail_closed_off_supported_platform(monkeypatch):
    monkeypatch.setattr("oceanx.sandbox.execution.get_platform", lambda: "unknown")

    capabilities = get_sandbox_execution_capabilities()

    assert capabilities.available is False
    assert capabilities.backend is None
    assert "macOS" in (capabilities.reason or "")


def test_python_runtime_fails_closed_when_explicit_interpreter_is_stale(
    tmp_path: Path, monkeypatch
) -> None:
    missing_launcher = tmp_path / "removed-ocean" / "bin" / "python"
    monkeypatch.setenv("OCEAN_SANDBOX_PYTHON", str(missing_launcher))

    with pytest.raises(SandboxUnavailableError, match="Python interpreter"):
        current_python_executable()


def test_python_runtime_defaults_to_named_oceanx_conda_environment(
    tmp_path: Path, monkeypatch
) -> None:
    prefix = tmp_path / "envs" / "oceanx"
    launcher = prefix / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(Path(sys.executable).resolve())
    monkeypatch.delenv("OCEAN_SANDBOX_PYTHON", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.setenv("CONDA_ENVS_PATH", str(tmp_path / "envs"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert current_python_executable() == launcher.absolute()


def test_python_launcher_resolves_parent_alias_without_leaving_environment(tmp_path, monkeypatch):
    from oceanx.sandbox.execution import _normalize_command

    real_home = tmp_path / "real-home"
    launcher = real_home / "env" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(Path(sys.executable).resolve())
    alias_home = tmp_path / "alias-home"
    alias_home.symlink_to(real_home, target_is_directory=True)
    alias_launcher = alias_home / "env" / "bin" / "python"
    monkeypatch.setenv("OCEAN_SANDBOX_PYTHON", str(alias_launcher))

    expected = launcher.parent.resolve() / "python"
    assert current_python_executable() == expected
    assert _normalize_command((str(alias_launcher), "-V")) == (str(expected), "-V")
    assert expected != launcher.resolve()


def test_macos_runtime_roots_include_homebrew_native_library_trees(tmp_path: Path) -> None:
    prefix = tmp_path / "homebrew"
    cellar = prefix / "Cellar"
    opt = prefix / "opt"
    python_prefix = cellar / "python@3.13" / "3.13.1"
    executable = python_prefix / "bin" / "python3.13"
    executable.parent.mkdir(parents=True)
    opt.mkdir(parents=True)
    executable.write_bytes(b"fixture")

    roots = _macos_package_manager_runtime_roots((executable, python_prefix))

    assert roots == tuple(sorted((cellar.resolve(), opt.resolve()), key=str))


def test_windows_execution_capabilities_remain_closed_when_broker_verification_fails(monkeypatch):
    monkeypatch.setattr("oceanx.sandbox.execution.get_platform", lambda: "windows")
    monkeypatch.setattr(
        "oceanx.sandbox.execution.verify_packaged_windows_broker",
        lambda: (None, "broker Authenticode verification failed"),
    )

    capabilities = get_sandbox_execution_capabilities()

    assert capabilities.available is False
    assert capabilities.backend is None
    assert capabilities.command is None
    assert capabilities.reason == "broker Authenticode verification failed"


def test_windows_execution_capabilities_require_a_verified_packaged_broker(tmp_path: Path, monkeypatch):
    broker = tmp_path / "ocean-sandbox-broker.exe"
    broker.write_bytes(b"fixture")
    monkeypatch.setattr("oceanx.sandbox.execution.get_platform", lambda: "windows")
    monkeypatch.setattr(
        "oceanx.sandbox.execution.verify_packaged_windows_broker",
        lambda: (
            WindowsBrokerInstallation(
                executable=broker,
                protocol_version="ocean-windows-sandbox-broker/v1",
                sha256="a" * 64,
            ),
            None,
        ),
    )

    capabilities = get_sandbox_execution_capabilities()

    assert capabilities.available is True
    assert capabilities.backend == "windows-appcontainer-job-v1"
    assert capabilities.command == str(broker)
    assert "job_kill_on_close" in capabilities.hard_limits


@pytest.mark.asyncio
async def test_windows_execution_dispatches_only_to_the_verified_broker(tmp_path: Path, monkeypatch):
    policy, work = _policy(tmp_path)
    executable = work / "python.exe"
    executable.write_bytes(b"fixture")
    broker = tmp_path / "ocean-sandbox-broker.exe"
    broker.write_bytes(b"fixture")
    captured: dict[str, object] = {}

    monkeypatch.setattr("oceanx.sandbox.execution.get_platform", lambda: "windows")
    monkeypatch.setattr(
        "oceanx.sandbox.execution.verify_packaged_windows_broker",
        lambda: (
            WindowsBrokerInstallation(
                executable=broker,
                protocol_version="ocean-windows-sandbox-broker/v1",
                sha256="a" * 64,
            ),
            None,
        ),
    )

    async def fake_broker_runner(**kwargs):
        captured.update(kwargs)
        return SandboxExecutionResult(
            status=SandboxExecutionStatus.SUCCEEDED,
            returncode=0,
            stdout=b"",
            stderr=b"",
            duration_seconds=0.01,
            trust=ExecutionTrust.SANDBOXED,
            limit_trigger=None,
            output_summary=OutputTreeSummary(0, 0, (), None),
        )

    monkeypatch.setattr("oceanx.sandbox.execution._run_windows_brokered_command", fake_broker_runner)

    result = await run_sandboxed_command(
        (str(executable), str(work / "analysis.py")),
        policy=policy,
        cwd=work,
        environment={"OUTPUT_DIR": str(policy.output_root)},
    )

    assert result.status is SandboxExecutionStatus.SUCCEEDED
    assert captured["command"] == (str(executable), str(work / "analysis.py"))
    assert captured["capabilities"].backend == "windows-appcontainer-job-v1"
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["OUTPUT_DIR"] == str(policy.output_root)
    assert "PATH" not in environment
    assert "HOME" not in environment
    assert "USERPROFILE" not in environment


@pytest.mark.asyncio
async def test_sandboxed_command_writes_only_declared_output_root(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("not mounted", encoding="utf-8")
    script = work / "analysis.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "try:\n"
        "    Path(os.environ['OUTSIDE_PATH']).read_text()\n"
        "except (PermissionError, FileNotFoundError):\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('outside path was readable')\n"
        "Path(os.environ['OUTPUT_DIR']).joinpath('result.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
        environment={
            "OUTSIDE_PATH": str(secret),
            "OUTPUT_DIR": str(policy.output_root),
        },
    )

    assert result.status is SandboxExecutionStatus.SUCCEEDED
    assert (policy.output_root / "result.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
async def test_sandboxed_command_cannot_read_outside_the_input_root_through_a_symlink(
    tmp_path: Path,
):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("not mounted", encoding="utf-8")
    escaped = work / "outside-link.txt"
    escaped.symlink_to(secret)
    script = work / "symlink_input.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "try:\n"
        "    Path(os.environ['ESCAPED_INPUT']).read_text()\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('outside input was readable through a symlink')\n"
        "Path(os.environ['OUTPUT_DIR']).joinpath('result.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
        environment={
            "ESCAPED_INPUT": str(escaped),
            "OUTPUT_DIR": str(policy.output_root),
        },
    )

    assert result.status is SandboxExecutionStatus.SUCCEEDED
    assert (policy.output_root / "result.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
async def test_sandboxed_command_never_publishes_a_symlinked_output(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path)
    script = work / "symlink_output.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "root = Path(os.environ['OUTPUT_DIR'])\n"
        "target = root / 'result.txt'\n"
        "target.write_text('ok')\n"
        "os.symlink(target, root / 'result-link.txt')\n",
        encoding="utf-8",
    )

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
        environment={"OUTPUT_DIR": str(policy.output_root)},
    )

    assert result.status is SandboxExecutionStatus.FAILED
    link = policy.output_root / "result-link.txt"
    if link.is_symlink():
        assert result.limit_trigger == "unsafe_output_tree"
    else:
        assert result.returncode not in (None, 0)


@pytest.mark.asyncio
async def test_sandboxed_command_never_publishes_a_hardlinked_output(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path)
    script = work / "hardlink_output.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "root = Path(os.environ['OUTPUT_DIR'])\n"
        "target = root / 'result.txt'\n"
        "target.write_text('ok')\n"
        "os.link(target, root / 'result-link.txt')\n",
        encoding="utf-8",
    )

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
        environment={"OUTPUT_DIR": str(policy.output_root)},
    )

    assert result.status is SandboxExecutionStatus.FAILED
    alias = policy.output_root / "result-link.txt"
    if alias.exists():
        assert result.limit_trigger == "unsafe_output_tree"
    else:
        assert result.returncode not in (None, 0)


@pytest.mark.asyncio
async def test_sandboxed_command_never_publishes_an_ads_shaped_output(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path)
    script = work / "ads_output.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OUTPUT_DIR']).joinpath('result.nc:metadata').write_text('unsafe')\n",
        encoding="utf-8",
    )

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
        environment={"OUTPUT_DIR": str(policy.output_root)},
    )

    assert result.status is SandboxExecutionStatus.FAILED
    assert result.limit_trigger == "unsafe_output_tree"
    assert result.output_summary.unsafe_entries == ("alternate_data_stream:result.nc:metadata",)


@pytest.mark.asyncio
async def test_sandboxed_command_stops_on_stdout_budget(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path, limits=_resource_limits(stdout_bytes=128))
    script = work / "noisy.py"
    script.write_text("print('x' * 4096)\n", encoding="utf-8")

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
    )

    assert result.status is SandboxExecutionStatus.RESOURCE_LIMITED
    assert result.limit_trigger == "stdout_bytes"
    assert len(result.stdout) == 128


@pytest.mark.asyncio
async def test_sandboxed_command_stops_at_wall_time_limit(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path, limits=_resource_limits(wall_time_seconds=0.15))
    script = work / "slow.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
    )

    assert result.status is SandboxExecutionStatus.TIMED_OUT
    assert result.limit_trigger == "wall_time"


@pytest.mark.asyncio
async def test_cancelling_sandboxed_command_terminates_its_process_group(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path, limits=_resource_limits(wall_time_seconds=30.0))
    pid_file = policy.output_root / "sandboxed.pid"
    script = work / "wait_for_cancel.py"
    script.write_text(
        "import os\n"
        "import time\n"
        "from pathlib import Path\n"
        "Path(os.environ['OUTPUT_DIR']).joinpath('sandboxed.pid').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    execution = asyncio.create_task(
        run_sandboxed_command(
            _python_command(script),
            policy=policy,
            cwd=work,
            environment={"OUTPUT_DIR": str(policy.output_root)},
        )
    )
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    else:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        raise AssertionError("Sandboxed fixture never wrote its process identifier")

    pid = int(pid_file.read_text(encoding="utf-8"))
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError("Cancelled sandbox process remained alive")


@pytest.mark.asyncio
async def test_sandboxed_command_stops_at_memory_ceiling(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(
        tmp_path,
        limits=_resource_limits(memory_bytes=33_554_432, memory_poll_interval_seconds=0.02),
    )
    script = work / "memory.py"
    script.write_text(
        "import time\npayload = bytearray(134_217_728)\ntime.sleep(2)\n",
        encoding="utf-8",
    )

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
    )

    assert result.status is SandboxExecutionStatus.RESOURCE_LIMITED
    assert result.limit_trigger == "memory_bytes"


@pytest.mark.asyncio
async def test_sandboxed_command_stops_at_file_size_limit(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path, limits=_resource_limits(disk_bytes=1_024))
    script = work / "disk.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['OUTPUT_DIR']).joinpath('large.bin').write_bytes(b'x' * 8192)\n",
        encoding="utf-8",
    )

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
        environment={"OUTPUT_DIR": str(policy.output_root)},
    )

    assert result.status is SandboxExecutionStatus.RESOURCE_LIMITED
    assert result.limit_trigger == "disk_bytes"


@pytest.mark.asyncio
async def test_networked_code_downloads_but_cannot_modify_sources(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available or sys.platform == "win32":
        pytest.skip(capabilities.reason or "Requires macOS or Linux sandbox")

    requests = []

    async def serve(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\n1,2,3\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        policy, work = _policy(tmp_path)
        policy = replace(policy, allow_network=True)
        source = work / "original.txt"
        source.write_text("original", encoding="utf-8")
        secret = tmp_path / "unrelated.txt"
        secret.write_text("private", encoding="utf-8")
        script = work / "download.py"
        script.write_text(
            "import pathlib, sys, urllib.request\n"
            "source, secret, target = map(pathlib.Path, sys.argv[2:])\n"
            "assert source.read_text() == 'original'\n"
            "with urllib.request.urlopen(sys.argv[1], timeout=2) as response:\n"
            "    target.write_bytes(response.read())\n"
            "assert sum(map(int, target.read_text().strip().split(','))) == 6\n"
            "for action in (lambda: source.write_text('changed'), source.unlink, secret.read_text):\n"
            "    try: action()\n"
            "    except OSError: pass\n"
            "    else: raise AssertionError('filesystem boundary lost')\n",
            encoding="utf-8",
        )
        port = server.sockets[0].getsockname()[1]
        target = policy.output_root / "download.csv"
        result = await run_sandboxed_command(
            _python_command(script, f"http://127.0.0.1:{port}/data.csv",
                            str(source), str(secret), str(target)),
            policy=policy, cwd=work,
        )
    finally:
        server.close()
        await server.wait_closed()
    assert result.status is SandboxExecutionStatus.SUCCEEDED, result.stderr.decode()
    assert source.read_text() == "original"
    assert target.read_text() == "1,2,3\n"
    assert len(requests) == 1 and requests[0].startswith(b"GET /data.csv ")


def test_network_permission_does_not_change_macos_filesystem_rules(tmp_path):
    policy, _ = _policy(tmp_path)
    offline = build_macos_seatbelt_profile(policy)
    online = build_macos_seatbelt_profile(replace(policy, allow_network=True))
    assert "(deny network*)" in offline
    assert "(allow network*)" in online
    assert "(system-network)" in online
    assert [line for line in offline.splitlines() if "file-" in line] == [
        line for line in online.splitlines() if "file-" in line
    ]


def test_homebrew_runtime_exposes_public_ca_not_private_configuration(tmp_path):
    prefix = tmp_path / "brew"
    runtime = prefix / "Cellar" / "python" / "3.13"
    runtime.mkdir(parents=True)
    openssl = prefix / "etc" / "openssl@3"
    openssl.mkdir(parents=True)
    bundle = openssl / "cert.pem"
    bundle.write_text("public CA fixture")
    private = openssl / "private"
    private.mkdir()
    roots = _macos_package_manager_runtime_roots((runtime,))
    assert bundle in roots
    assert openssl not in roots and private not in roots and prefix / "etc" not in roots


@pytest.mark.skipif(not os.environ.get("OCEAN_TEST_PUBLIC_HTTPS"), reason="Opt-in live HTTPS smoke test")
async def test_networked_code_public_https(tmp_path):
    policy, work = _policy(tmp_path, limits=_resource_limits(wall_time_seconds=30, stderr_bytes=16384))
    policy = replace(policy, allow_network=True)
    script = work / "https.py"
    script.write_text(
        "import pathlib, ssl, sys, urllib.request\n"
        "print(ssl.get_default_verify_paths(), ssl.create_default_context().cert_store_stats(), flush=True)\n"
        "with urllib.request.urlopen(sys.argv[1], timeout=15) as response:\n"
        "    assert response.status == 200\n"
        "    pathlib.Path(sys.argv[2]).write_bytes(response.read(1024))\n",
        encoding="utf-8",
    )
    result = await run_sandboxed_command(
        _python_command(script, os.environ["OCEAN_TEST_PUBLIC_HTTPS"],
                        str(policy.output_root / "public.txt")),
        policy=policy, cwd=work,
    )
    assert result.status is SandboxExecutionStatus.SUCCEEDED, result.stdout.decode() + result.stderr.decode()
    assert (policy.output_root / "public.txt").stat().st_size > 0


async def test_sandboxed_command_cannot_open_network_connections(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    opened_connection = False

    async def handle_connection(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal opened_connection
        opened_connection = True
        writer.close()
        await writer.wait_closed()
        del reader

    server = await asyncio.start_server(handle_connection, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        policy, work = _policy(tmp_path)
        script = work / "network.py"
        script.write_text(
            "import socket\n"
            "import sys\n"
            "try:\n"
            "    socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=0.2)\n"
            "except PermissionError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('network access was granted')\n",
            encoding="utf-8",
        )

        result = await run_sandboxed_command(
            _python_command(script, str(port)),
            policy=policy,
            cwd=work,
        )
    finally:
        server.close()
        await server.wait_closed()

    assert result.status is SandboxExecutionStatus.SUCCEEDED
    assert opened_connection is False


@pytest.mark.asyncio
async def test_sandboxed_command_cannot_fork_child_processes(tmp_path: Path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        pytest.skip(capabilities.reason or "sandbox backend is unavailable")

    policy, work = _policy(tmp_path)
    script = work / "fork.py"
    script.write_text(
        "import subprocess\n"
        "import sys\n"
        "try:\n"
        "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('child process was allowed')\n",
        encoding="utf-8",
    )

    result = await run_sandboxed_command(
        _python_command(script),
        policy=policy,
        cwd=work,
    )

    assert result.status is SandboxExecutionStatus.SUCCEEDED
