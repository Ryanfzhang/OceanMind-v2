"""Linux sandbox assembly everywhere; real isolation contract on Linux CI."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ocean_partner.sandbox import (
    ResourceLimits,
    SandboxExecutionPolicy,
    SandboxExecutionStatus,
    SandboxUnavailableError,
    get_sandbox_execution_capabilities,
    run_sandboxed_command,
)
from ocean_partner.sandbox.linux import build_bubblewrap_command, seccomp_filter


def policy_for(tmp_path):
    for name in ("input", "out", "tmp"):
        (tmp_path / name).mkdir()
    return SandboxExecutionPolicy(
        read_only_roots=(tmp_path / "input",),
        runtime_read_roots=(Path(sys.prefix), Path(sys.base_prefix)),
        writable_roots=(tmp_path / "out", tmp_path / "tmp"),
        output_root=tmp_path / "out", temporary_root=tmp_path / "tmp",
        limits=ResourceLimits(process_count=64, open_files=128, wall_time_seconds=10),
    )


def test_linux_mounts_are_explicit_readonly_and_network_unshared(tmp_path):
    policy = policy_for(tmp_path)
    args = build_bubblewrap_command("/usr/bin/bwrap", [sys.executable, "-c", "pass"],
                                    policy=policy, cwd=tmp_path / "input", seccomp_fd=8)
    assert "--unshare-all" in args and "--unshare-user" in args
    assert "--die-with-parent" in args
    assert args[args.index("--seccomp") + 1] == "8"
    mounts = [(arg, args[i + 1], args[i + 2]) for i, arg in enumerate(args) if arg in {"--ro-bind", "--bind"}]
    assert ("--ro-bind", str(tmp_path / "input"), str(tmp_path / "input")) in mounts
    assert ("--bind", str(tmp_path / "out"), str(tmp_path / "out")) in mounts
    assert not any(source in {"/", "/home", "/tmp", "/etc"} for _, source, _ in mounts)


def test_linux_missing_bwrap_fails_closed(monkeypatch):
    monkeypatch.setattr("ocean_partner.sandbox.execution.get_platform", lambda: "linux")
    monkeypatch.setattr("ocean_partner.sandbox.execution.shutil.which", lambda _: None)
    capabilities = get_sandbox_execution_capabilities()
    assert not capabilities.available
    assert "bubblewrap" in capabilities.reason


def test_networked_linux_keeps_source_mounts_readonly(tmp_path):
    policy = replace(policy_for(tmp_path), allow_network=True)
    args = build_bubblewrap_command(
        "/usr/bin/bwrap", [sys.executable, "-c", "pass"],
        policy=policy, cwd=tmp_path / "input", seccomp_fd=8,
    )
    assert args.index("--share-net") > args.index("--unshare-all")
    mounts = [(arg, args[i + 1]) for i, arg in enumerate(args)
              if arg in {"--ro-bind", "--bind"}]
    assert ("--ro-bind", str(tmp_path / "input")) in mounts
    assert ("--bind", str(tmp_path / "out")) in mounts
    for system_file in ("/etc/resolv.conf", "/etc/ssl/certs"):
        if Path(system_file).exists():
            assert ("--ro-bind", system_file) in mounts
    assert not any(source in {"/", "/home", "/etc"} for _, source in mounts)


def test_linux_missing_seccomp_fails_closed(monkeypatch):
    monkeypatch.setattr("ocean_partner.sandbox.linux.ctypes.util.find_library", lambda _: None)
    with pytest.raises(SandboxUnavailableError, match="libseccomp"), seccomp_filter(allow_child_processes=False):
        pass


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Requires an actual Linux namespace kernel")
async def test_native_linux_readonly_network_fork_and_secret_isolation(tmp_path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        if os.environ.get("OCEAN_REQUIRE_LINUX_SANDBOX") == "1":
            pytest.fail(capabilities.reason)
        pytest.skip(capabilities.reason)
    policy = policy_for(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    source = tmp_path / "input" / "data.txt"
    source.write_text("shared data")
    code = '''
import os, socket
from pathlib import Path
source, secret, output = map(Path, __import__('sys').argv[1:])
assert source.read_text() == 'shared data'
for path in (source, secret):
    try: path.write_text('changed')
    except OSError: pass
    else: raise AssertionError('outside write allowed')
try: secret.read_text()
except OSError: pass
else: raise AssertionError('secret read allowed')
try: socket.socket().connect(('1.1.1.1', 443))
except OSError: pass
else: raise AssertionError('network allowed')
try: child = os.fork()
except OSError: pass
else: raise AssertionError('fork allowed')
assert not os.environ.get('OCEAN_TEST_SECRET')
# Async scientific stores use an anonymous socketpair for their event loop.
# It must work even though opening network sockets is forbidden.
import asyncio
loop = asyncio.new_event_loop()
loop.close()
(output / 'ok.txt').write_text('ok')
'''
    os.environ["OCEAN_TEST_SECRET"] = "never-inherit"
    try:
        result = await run_sandboxed_command(
            [sys.executable, "-c", code, str(source), str(secret), str(policy.output_root)],
            policy=policy, cwd=tmp_path / "input",
        )
    finally:
        os.environ.pop("OCEAN_TEST_SECRET", None)
    assert result.status == SandboxExecutionStatus.SUCCEEDED, result.stderr.decode()
    assert source.read_text() == "shared data"
    assert secret.read_text() == "private"
    assert (policy.output_root / "ok.txt").read_text() == "ok"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Requires an actual Linux namespace kernel")
async def test_native_linux_scientific_netcdf_zarr_and_plot(tmp_path):
    capabilities = get_sandbox_execution_capabilities()
    if not capabilities.available:
        if os.environ.get("OCEAN_REQUIRE_LINUX_SANDBOX") == "1":
            pytest.fail(capabilities.reason)
        pytest.skip(capabilities.reason)
    policy = policy_for(tmp_path)
    code = '''
import numpy as np, xarray as xr, zarr
import matplotlib.pyplot as plt
from pathlib import Path
out = Path(__import__('sys').argv[1])
ds = xr.Dataset({'chlorophyll': ('time', np.array([1., 2., 3.]))})
ds.to_netcdf(out / 'analysis.nc')
with xr.open_dataset(out / 'analysis.nc') as actual:
    assert float(actual.chlorophyll.mean()) == 2.
zarr.save_array(str(out / 'analysis.zarr'), np.array([1., 2., 3.]))
assert float(zarr.open_array(str(out / 'analysis.zarr'))[:].mean()) == 2.
fig, ax = plt.subplots()
ax.plot([1, 2, 3])
fig.savefig(out / 'figure.png')
plt.close(fig)
'''
    result = await run_sandboxed_command(
        [sys.executable, "-c", code, str(policy.output_root)],
        policy=policy, cwd=tmp_path / "input",
    )
    assert result.status == SandboxExecutionStatus.SUCCEEDED, result.stderr.decode()
    assert (policy.output_root / "analysis.nc").is_file()
    assert (policy.output_root / "figure.png").is_file()
