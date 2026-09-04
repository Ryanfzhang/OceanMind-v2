"""Small OceanMind-owned execution confirmation policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""


class OceanExecutionPermissionChecker:
    _EXECUTION_TOOLS = frozenset({"ocean_expert_run_code"})

    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
    ) -> PermissionDecision:
        del file_path, command
        if is_read_only or tool_name not in self._EXECUTION_TOOLS:
            return PermissionDecision(allowed=True)
        return PermissionDecision(
            allowed=False,
            requires_confirmation=True,
            reason="An OceanMind Expert is ready to run bounded scientific code locally.",
        )


__all__ = ["OceanExecutionPermissionChecker", "PermissionDecision"]
