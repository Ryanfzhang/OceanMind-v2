"""Linux namespaces and native seccomp for the existing execution policy.

No host-root mount or unsandboxed fallback. Bubblewrap creates a private PID,
mount namespace; networking is controlled by policy. Only OS/runtime files and
declared roots are visible.
See https://github.com/containers/bubblewrap/blob/main/bwrap.xml.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from ocean_partner.sandbox.errors import SandboxUnavailableError

if TYPE_CHECKING:
    from ocean_partner.sandbox.execution import SandboxExecutionPolicy


class _ArgCompare(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint), ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64),
    ]


def _seccomp_library() -> ctypes.CDLL:
    name = ctypes.util.find_library("seccomp")
    if not name:
        raise SandboxUnavailableError("Linux execution requires libseccomp (libseccomp2)")
    try:
        lib = ctypes.CDLL(name, use_errno=True)
        lib.seccomp_init.argtypes = [ctypes.c_uint32]
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
        lib.seccomp_rule_add_array.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
            ctypes.c_uint, ctypes.POINTER(_ArgCompare),
        ]
        lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return lib
    except (OSError, AttributeError) as exc:
        raise SandboxUnavailableError(f"Linux libseccomp is unusable: {exc}") from exc


@contextmanager
def seccomp_filter(*, allow_child_processes: bool, allow_network: bool = False) -> Iterator[BinaryIO]:
    """Export native-architecture cBPF, keeping the descriptor alive through spawn.

    Thread creation is allowed; process creation is denied unless the policy
    explicitly allows it. clone3 returns ENOSYS so glibc can use filtered clone.
    Session/group changes are denied so descendants remain cancellable and their
    combined RSS can be monitored. The PID namespace also owns their lifetime.
    """
    lib = _seccomp_library()
    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise SandboxUnavailableError("Cannot allocate Linux seccomp filter")

    def deny(name: str, error: int = errno.EPERM, comparison: _ArgCompare | None = None) -> None:
        syscall = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
        if syscall < 0:  # Some architectures have clone, but no fork/vfork.
            return
        result = lib.seccomp_rule_add_array(
            context, 0x00050000 | error, syscall,
            int(comparison is not None),
            ctypes.pointer(comparison) if comparison is not None else None,
        )
        if result < 0:
            raise SandboxUnavailableError(f"Cannot restrict Linux syscall {name}: {result}")

    try:
        for syscall in (
            "unshare", "setns", "mount", "umount2", "pivot_root", "ptrace",
            "setsid", "setpgid",
        ):
            deny(syscall)
        if not allow_network:
            deny("socket")
        deny("clone3", errno.ENOSYS)
        # Forbid creation of new namespaces even when child processes are allowed.
        for flag in (0x00020000, 0x02000000, 0x04000000, 0x08000000,
                     0x10000000, 0x20000000, 0x40000000):
            deny("clone", comparison=_ArgCompare(0, 7, flag, flag))  # MASKED_EQ
        if not allow_child_processes:
            deny("fork")
            deny("vfork")
            deny("clone", comparison=_ArgCompare(0, 7, 0x00010000, 0))  # !CLONE_THREAD
        with tempfile.TemporaryFile() as handle:
            if lib.seccomp_export_bpf(context, handle.fileno()) < 0:
                raise SandboxUnavailableError("Cannot export Linux seccomp filter")
            handle.seek(0)
            yield handle
    finally:
        lib.seccomp_release(context)


def build_bubblewrap_command(
    executable: str, command: Sequence[str], *,
    policy: SandboxExecutionPolicy, cwd: Path, seccomp_fd: int,
) -> tuple[str, ...]:
    argv = [
        executable, "--unshare-all", "--unshare-user", "--die-with-parent",
        "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev",
    ]
    if policy.allow_network:
        argv.append("--share-net")
    # Do not mount /, /home, /run, /tmp or all of /etc from the host.
    system_paths = (
        "/usr", "/bin", "/sbin", "/lib", "/lib64",
        "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d",
        "/etc/fonts", "/etc/localtime",
    )
    if policy.allow_network:
        system_paths += ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf",
                         "/etc/ssl/certs", "/etc/pki/tls/certs")
    mounts: dict[Path, bool] = {Path(p): False for p in system_paths if Path(p).exists()}
    for root in (*policy.runtime_read_roots, *policy.read_only_roots):
        mounts[Path(root)] = False
    for root in policy.writable_roots:
        mounts[Path(root)] = True
    for root, writable in sorted(mounts.items(), key=lambda item: (len(item[0].parts), str(item[0]))):
        if root == Path("/") or root == Path("/proc") or root == Path("/dev"):
            raise SandboxUnavailableError(f"Unsafe Linux sandbox mount: {root}")
        argv.extend(("--bind" if writable else "--ro-bind", str(root), str(root)))
    argv.extend(("--chdir", str(cwd), "--seccomp", str(seccomp_fd), "--", *command))
    return tuple(argv)
