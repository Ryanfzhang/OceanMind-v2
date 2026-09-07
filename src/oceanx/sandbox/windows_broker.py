"""Versioned, bounded protocol for the native Windows sandbox broker.

The broker is intentionally a separate signed executable.  This module owns
the sidecar-facing JSON contract so Python never constructs a shell command or
selects Windows security capabilities at runtime.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oceanx.sandbox.execution import SandboxExecutionPolicy


BROKER_PROTOCOL_VERSION = "ocean-windows-sandbox-broker/v1"
BROKER_RESPONSE_VERSION = "ocean-windows-sandbox-broker-result/v1"
BROKER_RELEASE_MANIFEST_VERSION = "ocean-windows-sandbox-broker-release/v1"
BROKER_SELF_CHECK_VERSION = "ocean-windows-sandbox-broker-self-check/v1"
BROKER_EXECUTABLE_NAME = "ocean-sandbox-broker.exe"
BROKER_RELEASE_MANIFEST_NAME = "ocean-sandbox-broker.release.json"
BROKER_MAX_ARGUMENTS = 128
BROKER_MAX_ENVIRONMENT_ENTRIES = 64
BROKER_MAX_PATHS_PER_ROOT_KIND = 64
BROKER_MAX_STREAM_BYTES = 8 * 1024 * 1024
BROKER_MAX_JSON_BYTES = 512 * 1024


class WindowsBrokerProtocolError(ValueError):
    """A broker request or result did not satisfy the frozen wire contract."""


@dataclass(frozen=True)
class WindowsBrokerInstallation:
    """A packaged native broker that has passed local release preflight."""

    executable: Path
    protocol_version: str
    sha256: str


def verify_packaged_windows_broker() -> tuple[WindowsBrokerInstallation | None, str | None]:
    """Return a trusted packaged broker only after all local gates pass.

    There is deliberately no environment-variable path override.  A renderer
    or inherited shell must not replace the broker selected by the frozen
    sidecar.  The release manifest is an input to the signed desktop bundle;
    it pins both the binary digest and the protocol version before broker
    self-check is allowed to enable AnalysisRun execution.
    """

    if os.name != "nt":
        return None, "Windows sandbox broker is only available on native Windows"
    if not getattr(sys, "frozen", False):
        return None, "Windows sandbox broker requires a frozen packaged sidecar"
    executable = Path(sys.executable).resolve().with_name(BROKER_EXECUTABLE_NAME)
    manifest_path = executable.with_name(BROKER_RELEASE_MANIFEST_NAME)
    if not executable.is_file() or not manifest_path.is_file():
        return None, "Packaged Windows sandbox broker or release manifest is missing"
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "Packaged Windows sandbox broker release manifest is invalid"
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "protocol_version",
        "sha256",
        "authenticode_required",
    }:
        return None, "Packaged Windows sandbox broker release manifest is incompatible"
    if (
        manifest.get("schema_version") != BROKER_RELEASE_MANIFEST_VERSION
        or manifest.get("protocol_version") != BROKER_PROTOCOL_VERSION
        or manifest.get("authenticode_required") is not True
    ):
        return None, "Packaged Windows sandbox broker release policy is incompatible"
    expected_sha256 = manifest.get("sha256")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        return None, "Packaged Windows sandbox broker release digest is invalid"
    actual_sha256 = _sha256_file(executable)
    if actual_sha256 is None or actual_sha256.lower() != expected_sha256.lower():
        return None, "Packaged Windows sandbox broker digest does not match the release manifest"
    if not _verify_authenticode(executable):
        return None, "Packaged Windows sandbox broker Authenticode verification failed"
    if not _broker_self_check(executable):
        return None, "Packaged Windows sandbox broker self-check failed"
    return WindowsBrokerInstallation(
        executable=executable,
        protocol_version=BROKER_PROTOCOL_VERSION,
        sha256=actual_sha256,
    ), None


@dataclass(frozen=True)
class WindowsBrokerResult:
    """Broker result after strict response validation.

    Output-tree safety remains a Python-side postflight operation: the broker
    only reports the process and Job Object outcome, never an unbounded file
    listing or a host filesystem path.
    """

    status: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    limit_trigger: str | None
    job_terminated: bool


def build_windows_broker_request(
    command: Sequence[str],
    *,
    policy: SandboxExecutionPolicy,
    cwd: Path,
    environment: Mapping[str, str],
) -> bytes:
    """Serialize one exact policy-owned execution request for the broker.

    The caller must already have normalized command/cwd/policy paths.  The
    second validation here intentionally remains strict because the broker
    boundary must not rely on a renderer, model, or an incidental caller to
    provide a safe shape.
    """

    if not command or len(command) > BROKER_MAX_ARGUMENTS:
        raise WindowsBrokerProtocolError("Broker command must contain 1--128 argv items")
    executable = _absolute_path(command[0], field="command[0]")
    arguments = [_text(argument, field=f"command[{index}]") for index, argument in enumerate(command[1:], 1)]
    roots = {
        "read_only": _paths(policy.read_only_roots, field="read_only_roots"),
        "runtime_read": _paths(policy.runtime_read_roots, field="runtime_read_roots"),
        "writable": _paths(policy.writable_roots, field="writable_roots"),
    }
    payload = {
        "schema_version": BROKER_PROTOCOL_VERSION,
        "executable": executable,
        "arguments": arguments,
        "cwd": _absolute_path(cwd, field="cwd"),
        "roots": roots,
        "output_root": _absolute_path(policy.output_root, field="output_root"),
        "temporary_root": _absolute_path(policy.temporary_root, field="temporary_root"),
        "environment": _environment(environment),
        "limits": _limits(policy.limits),
        "allow_child_processes": bool(policy.allow_child_processes),
    }
    return _encode(payload)


def parse_windows_broker_result(raw: bytes) -> WindowsBrokerResult:
    """Parse one bounded broker result without accepting extension fields."""

    payload = _decode(raw)
    _exact_keys(
        payload,
        {
            "schema_version",
            "status",
            "returncode",
            "stdout_base64",
            "stderr_base64",
            "duration_seconds",
            "limit_trigger",
            "job_terminated",
        },
        field="broker result",
    )
    if payload.get("schema_version") != BROKER_RESPONSE_VERSION:
        raise WindowsBrokerProtocolError("Broker result schema version is incompatible")
    status = payload.get("status")
    if status not in {"succeeded", "failed", "timed_out", "resource_limited", "cancelled"}:
        raise WindowsBrokerProtocolError("Broker result status is invalid")
    returncode = payload.get("returncode")
    if returncode is not None and (
        not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or returncode < -(2**31)
        or returncode > 2**31 - 1
    ):
        raise WindowsBrokerProtocolError("Broker result returncode must be an integer or null")
    duration = payload.get("duration_seconds")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise WindowsBrokerProtocolError("Broker result duration_seconds must be non-negative")
    limit_trigger = payload.get("limit_trigger")
    if limit_trigger is not None and (
        not isinstance(limit_trigger, str)
        or not limit_trigger
        or len(limit_trigger) > 64
        or not limit_trigger.isascii()
        or not limit_trigger.replace("_", "").isalnum()
    ):
        raise WindowsBrokerProtocolError("Broker result limit_trigger is invalid")
    job_terminated = payload.get("job_terminated")
    if not isinstance(job_terminated, bool):
        raise WindowsBrokerProtocolError("Broker result job_terminated must be boolean")
    if not job_terminated:
        raise WindowsBrokerProtocolError("Broker result does not prove Job Object cleanup")
    if status == "succeeded":
        if returncode != 0 or limit_trigger is not None:
            raise WindowsBrokerProtocolError("Broker success result is internally inconsistent")
    else:
        if returncode == 0:
            raise WindowsBrokerProtocolError("Broker non-success result is internally inconsistent")
        if status in {"timed_out", "resource_limited"} and limit_trigger is None:
            raise WindowsBrokerProtocolError("Broker limited result is missing its limit trigger")
    return WindowsBrokerResult(
        status=status,
        returncode=returncode,
        stdout=_decode_stream(payload.get("stdout_base64"), field="stdout_base64"),
        stderr=_decode_stream(payload.get("stderr_base64"), field="stderr_base64"),
        duration_seconds=float(duration),
        limit_trigger=limit_trigger,
        job_terminated=job_terminated,
    )


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _broker_self_check(executable: Path) -> bool:
    try:
        completed = subprocess.run(
            [str(executable), "self-check"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=20,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return False
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > BROKER_MAX_JSON_BYTES:
        return False
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False
    return payload == {
        "schema_version": BROKER_SELF_CHECK_VERSION,
        "protocol_version": BROKER_PROTOCOL_VERSION,
        "passed": True,
        "checks": {
            "app_container": True,
            "declared_output_written": True,
            "outside_read_denied": True,
            "network_denied": True,
            "job_kill_on_close": True,
        },
    }


def _verify_authenticode(path: Path) -> bool:
    """Use Windows trust APIs without spawning a shell or network helper."""

    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover - native Windows always has ctypes.
        return False

    class Guid(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class WinTrustFileInfo(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.c_void_p),
        ]

    class WinTrustData(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.POINTER(WinTrustFileInfo)),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        ]

    file_info = WinTrustFileInfo(
        cbStruct=ctypes.sizeof(WinTrustFileInfo),
        pcwszFilePath=str(path),
        hFile=None,
        pgKnownSubject=None,
    )
    data = WinTrustData(
        cbStruct=ctypes.sizeof(WinTrustData),
        pPolicyCallbackData=None,
        pSIPClientData=None,
        dwUIChoice=2,  # WTD_UI_NONE
        fdwRevocationChecks=0,  # WTD_REVOKE_NONE; release signing verifies revocation policy separately.
        dwUnionChoice=1,  # WTD_CHOICE_FILE
        pFile=ctypes.pointer(file_info),
        dwStateAction=0,  # WTD_STATEACTION_IGNORE
        hWVTStateData=None,
        pwszURLReference=None,
        dwProvFlags=0x00001000,  # WTD_CACHE_ONLY_URL_RETRIEVAL: do not create a network escape.
        dwUIContext=0,
        pSignatureSettings=None,
    )
    action = Guid(
        Data1=0x00AAC56B,
        Data2=0xCD44,
        Data3=0x11D0,
        Data4=(ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    try:
        verify = ctypes.WinDLL("wintrust", use_last_error=True).WinVerifyTrust
        verify.argtypes = [wintypes.HWND, ctypes.POINTER(Guid), ctypes.POINTER(WinTrustData)]
        verify.restype = ctypes.c_long
        return verify(None, ctypes.byref(action), ctypes.byref(data)) == 0
    except OSError:
        return False


def _paths(values: Sequence[Path | str], *, field: str) -> list[str]:
    if not values or len(values) > BROKER_MAX_PATHS_PER_ROOT_KIND:
        raise WindowsBrokerProtocolError(f"{field} must contain 1--64 roots")
    return [_absolute_path(value, field=f"{field}[{index}]") for index, value in enumerate(values)]


def _environment(values: Mapping[str, str]) -> dict[str, str]:
    if len(values) > BROKER_MAX_ENVIRONMENT_ENTRIES:
        raise WindowsBrokerProtocolError("Broker environment contains too many entries")
    result: dict[str, str] = {}
    normalized_names: set[str] = set()
    for key, value in values.items():
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 128
            or not key.isascii()
            or not all(character.isalnum() or character == "_" for character in key)
        ):
            raise WindowsBrokerProtocolError("Broker environment key is invalid")
        normalized = key.upper()
        if normalized in normalized_names:
            raise WindowsBrokerProtocolError("Broker environment contains case-colliding keys")
        normalized_names.add(normalized)
        result[key] = _text(value, field=f"environment[{key}]")
    return dict(sorted(result.items(), key=lambda item: item[0].upper()))


def _limits(limits: Any) -> dict[str, int | float]:
    names = (
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
        "termination_grace_seconds",
    )
    result: dict[str, int | float] = {}
    for name in names:
        value = getattr(limits, name, None)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise WindowsBrokerProtocolError(f"Broker limit {name} is invalid")
        result[name] = value
    return result


def _absolute_path(value: Path | str, *, field: str) -> str:
    candidate = Path(value)
    if not candidate.is_absolute() or "\x00" in str(candidate):
        raise WindowsBrokerProtocolError(f"{field} must be an absolute path")
    return str(candidate)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 32_768 or "\x00" in value:
        raise WindowsBrokerProtocolError(f"{field} must be a non-empty bounded string")
    return value


def _encode(payload: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WindowsBrokerProtocolError("Broker request contains non-canonical JSON values") from exc
    if len(encoded) > BROKER_MAX_JSON_BYTES:
        raise WindowsBrokerProtocolError("Broker request exceeds the JSON size limit")
    return encoded


def _reject_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _decode(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > BROKER_MAX_JSON_BYTES:
        raise WindowsBrokerProtocolError("Broker result exceeds the JSON size limit")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WindowsBrokerProtocolError("Broker result is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise WindowsBrokerProtocolError("Broker result must be an object")
    return payload


def _exact_keys(payload: Mapping[str, Any], expected: set[str], *, field: str) -> None:
    if set(payload) != expected:
        raise WindowsBrokerProtocolError(f"{field} fields are incompatible")


def _decode_stream(value: object, *, field: str) -> bytes:
    if not isinstance(value, str):
        raise WindowsBrokerProtocolError(f"Broker result {field} must be a base64 string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise WindowsBrokerProtocolError(f"Broker result {field} is not valid base64") from exc
    if len(decoded) > BROKER_MAX_STREAM_BYTES:
        raise WindowsBrokerProtocolError(f"Broker result {field} exceeds the stream limit")
    return decoded


__all__ = [
    "BROKER_PROTOCOL_VERSION",
    "BROKER_RELEASE_MANIFEST_VERSION",
    "BROKER_RESPONSE_VERSION",
    "BROKER_SELF_CHECK_VERSION",
    "WindowsBrokerInstallation",
    "WindowsBrokerProtocolError",
    "WindowsBrokerResult",
    "build_windows_broker_request",
    "parse_windows_broker_result",
    "verify_packaged_windows_broker",
]
