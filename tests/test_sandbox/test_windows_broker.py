"""Wire-contract tests for the native Windows analysis sandbox broker."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from ocean_partner.sandbox import ResourceLimits, SandboxExecutionPolicy
from ocean_partner.sandbox.windows_broker import (
    BROKER_PROTOCOL_VERSION,
    BROKER_RESPONSE_VERSION,
    WindowsBrokerProtocolError,
    build_windows_broker_request,
    parse_windows_broker_result,
)


def _policy(tmp_path: Path) -> tuple[SandboxExecutionPolicy, Path]:
    work = tmp_path / "work"
    runtime = tmp_path / "runtime"
    output = tmp_path / "output"
    temporary = tmp_path / "temporary"
    for path in (work, runtime, output, temporary):
        path.mkdir()
    executable = runtime / "python.exe"
    executable.write_bytes(b"fixture")
    return (
        SandboxExecutionPolicy(
            read_only_roots=(work,),
            runtime_read_roots=(runtime,),
            writable_roots=(output, temporary),
            output_root=output,
            temporary_root=temporary,
            limits=ResourceLimits(),
        ),
        executable,
    )


def test_broker_request_is_bounded_policy_owned_json(tmp_path: Path):
    policy, executable = _policy(tmp_path)

    request = json.loads(
        build_windows_broker_request(
            (str(executable), str(policy.read_only_roots[0] / "analysis.py")),
            policy=policy,
            cwd=policy.read_only_roots[0],
            environment={"PYTHONDONTWRITEBYTECODE": "1", "OUTPUT_DIR": str(policy.output_root)},
        )
    )

    assert request["schema_version"] == BROKER_PROTOCOL_VERSION
    assert request["executable"] == str(executable)
    assert request["arguments"] == [str(policy.read_only_roots[0] / "analysis.py")]
    assert request["roots"]["runtime_read"] == [str(policy.runtime_read_roots[0])]
    assert request["allow_child_processes"] is False
    assert request["limits"]["process_count"] == policy.limits.process_count


@pytest.mark.parametrize(
    ("command", "environment"),
    [
        (("python", "analysis.py"), {}),
        (("C:\\safe\\python.exe", "bad\x00argument"), {}),
        (("C:\\safe\\python.exe",), {"BAD=KEY": "value"}),
        (("C:\\safe\\python.exe",), {"OUTPUT_DIR": "one", "output_dir": "two"}),
        (("C:\\safe\\python.exe",), {"OCEAN-NAME": "value"}),
    ],
)
def test_broker_request_rejects_unsafe_command_or_environment(tmp_path: Path, command, environment):
    policy, _ = _policy(tmp_path)

    with pytest.raises(WindowsBrokerProtocolError):
        build_windows_broker_request(command, policy=policy, cwd=policy.read_only_roots[0], environment=environment)


@pytest.mark.parametrize(
    "environment",
    [
        {"OUTPUT_DIR": "one", "output_dir": "two"},
        {"OCEAN-NAME": "value"},
    ],
)
def test_broker_request_rejects_case_colliding_or_non_identifier_environment_keys(
    tmp_path: Path, environment
):
    policy, executable = _policy(tmp_path)

    with pytest.raises(WindowsBrokerProtocolError):
        build_windows_broker_request(
            (str(executable), str(policy.read_only_roots[0] / "analysis.py")),
            policy=policy,
            cwd=policy.read_only_roots[0],
            environment=environment,
        )


def test_broker_result_requires_an_exact_bounded_schema():
    result = parse_windows_broker_result(
        json.dumps(
            {
                "schema_version": BROKER_RESPONSE_VERSION,
                "status": "succeeded",
                "returncode": 0,
                "stdout_base64": base64.b64encode(b"ok").decode("ascii"),
                "stderr_base64": "",
                "duration_seconds": 0.25,
                "limit_trigger": None,
                "job_terminated": True,
            }
        ).encode("utf-8")
    )

    assert result.stdout == b"ok"
    assert result.stderr == b""
    assert result.job_terminated is True


def _valid_broker_result(**changes: object) -> dict[str, object]:
    return {
        "schema_version": BROKER_RESPONSE_VERSION,
        "status": "succeeded",
        "returncode": 0,
        "stdout_base64": base64.b64encode(b"ok").decode("ascii"),
        "stderr_base64": "",
        "duration_seconds": 0.25,
        "limit_trigger": None,
        "job_terminated": True,
    } | changes


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": BROKER_RESPONSE_VERSION},
        {
            "schema_version": BROKER_RESPONSE_VERSION,
            "status": "succeeded",
            "returncode": 0,
            "stdout_base64": "not base64!",
            "stderr_base64": "",
            "duration_seconds": 0.25,
            "limit_trigger": None,
            "job_terminated": True,
        },
    ],
)
def test_broker_result_rejects_partial_or_malformed_payloads(payload):
    with pytest.raises(WindowsBrokerProtocolError):
        parse_windows_broker_result(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    "payload",
    [
        _valid_broker_result(duration_seconds=float("nan")),
        _valid_broker_result(duration_seconds=float("inf")),
        _valid_broker_result(returncode=2**31),
        _valid_broker_result(returncode=-2**31 - 1),
        _valid_broker_result(returncode=1),
        _valid_broker_result(limit_trigger="broker_failure"),
        _valid_broker_result(job_terminated=False),
        _valid_broker_result(status="failed"),
        _valid_broker_result(status="timed_out", returncode=None),
        _valid_broker_result(status="resource_limited", returncode=None),
        _valid_broker_result(status="resource_limited", returncode=None, limit_trigger="bad trigger"),
    ],
)
def test_broker_result_rejects_nonfinite_or_internally_inconsistent_terminals(payload):
    with pytest.raises(WindowsBrokerProtocolError):
        parse_windows_broker_result(json.dumps(payload).encode("utf-8"))


def test_broker_result_rejects_duplicate_or_nonstandard_json_numbers():
    duplicate_status = (
        b'{"schema_version":"ocean-windows-sandbox-broker-result/v1",'
        b'"status":"failed","status":"succeeded","returncode":0,'
        b'"stdout_base64":"","stderr_base64":"","duration_seconds":0.25,'
        b'"limit_trigger":null,"job_terminated":true}'
    )
    nonfinite_duration = (
        b'{"schema_version":"ocean-windows-sandbox-broker-result/v1",'
        b'"status":"succeeded","returncode":0,'
        b'"stdout_base64":"","stderr_base64":"","duration_seconds":NaN,'
        b'"limit_trigger":null,"job_terminated":true}'
    )

    for raw in (duplicate_status, nonfinite_duration):
        with pytest.raises(WindowsBrokerProtocolError):
            parse_windows_broker_result(raw)
