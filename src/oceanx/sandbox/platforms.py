"""Small platform detector used only by the scientific execution sandbox."""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from functools import lru_cache
from typing import Literal

PlatformName = Literal["macos", "linux", "windows", "wsl", "unknown"]


def detect_platform(
    *,
    system_name: str | None = None,
    release: str | None = None,
    env: Mapping[str, str] | None = None,
) -> PlatformName:
    """Return the normalized platform name for this process."""

    environment = env or os.environ
    system = (system_name or platform.system()).lower()
    kernel_release = (release or platform.release()).lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        if (
            "microsoft" in kernel_release
            or environment.get("WSL_DISTRO_NAME")
            or environment.get("WSL_INTEROP")
        ):
            return "wsl"
        return "linux"
    return "unknown"


@lru_cache(maxsize=1)
def get_platform() -> PlatformName:
    """Return the detected platform for this process."""

    return detect_platform()


__all__ = ["PlatformName", "detect_platform", "get_platform"]
