"""Stable compatibility identities for the packaged Ocean Desktop pair.

This value describes durable desktop-sidecar state, not the Python package
version. Bump it whenever a release cannot safely continue an existing local
workspace/checkpoint without an explicit migration and rollback plan.
"""

from __future__ import annotations

from typing import Literal

DESKTOP_BACKEND_SCHEMA = "ocean-desktop-backend/v1"
DESKTOP_RUNTIME_CAPABILITY_SCHEMA = "ocean-desktop-runtime-capabilities/v2"

DesktopConnectionId = Literal[
    "expert_code_execution",
    "web_search",
    "jina_reader",
    "netcdf",
    "zarr",
    "gsw",
]
DesktopConnectionLabel = Literal[
    "Expert code",
    "Web search",
    "Jina Reader",
    "NetCDF",
    "Zarr",
    "TEOS-10",
]

# Keep the runtime projection and its Protocol v2 validator on one inventory.
# Adding a connection must not require updating an unrelated list-length guard.
DESKTOP_RUNTIME_CONNECTIONS: tuple[
    tuple[DesktopConnectionId, DesktopConnectionLabel], ...
] = (
    ("expert_code_execution", "Expert code"),
    ("web_search", "Web search"),
    ("jina_reader", "Jina Reader"),
    ("netcdf", "NetCDF"),
    ("zarr", "Zarr"),
    ("gsw", "TEOS-10"),
)


__all__ = [
    "DESKTOP_BACKEND_SCHEMA",
    "DESKTOP_RUNTIME_CAPABILITY_SCHEMA",
    "DESKTOP_RUNTIME_CONNECTIONS",
    "DesktopConnectionId",
    "DesktopConnectionLabel",
]
