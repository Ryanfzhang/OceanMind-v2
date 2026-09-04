"""Workspace-bound Deep Agents/LangGraph runtime construction."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ocean_partner.deep_runtime import build_deep_agent_engine
from ocean_partner.model_config import ModelRole, load_model_profile
from ocean_partner.runtime import (
    OceanRuntimeComposition,
    build_ocean_discussion_runtime,
    build_ocean_expert_runtime,
    build_ocean_runtime,
)
from ocean_partner.tools import OceanToolServices

OCEAN_EXPERT_MIN_RESPONSE_TOKENS = 24_576


@dataclass(frozen=True)
class OceanAgentBudget:
    """Request-level safety limits; LangGraph owns conversation state."""

    max_turns: int = 200
    max_tool_calls: int = 256
    max_wall_seconds: float = 1_800.0
    max_input_tokens: int = 1_000_000
    max_output_tokens: int = 200_000
    max_tool_wait_seconds: float = 3_900.0
    max_team_tokens: int = 12_000_000
    delivery_reserve_tokens: int = 400_000


class OceanAgentRuntimeError(RuntimeError):
    """The configured model or checkpoint runtime cannot start."""


@dataclass
class OceanAgentRuntime:
    provider_id: str
    model_id: str
    engine: Any
    base_system_prompt: str
    _close: Callable[[], Awaitable[None]]

    async def close(self) -> None:
        await self._close()


OceanAgentRuntimeFactory = Callable[
    [OceanToolServices, Path, OceanAgentBudget, Callable[[str, str, str], str]],
    Awaitable[OceanAgentRuntime],
]


def configured_provider_id(role: ModelRole = "coordinator") -> str:
    try:
        return load_model_profile(role).provider
    except (OSError, ValueError) as exc:
        raise OceanAgentRuntimeError(str(exc)) from exc


def configured_model_id(role: ModelRole = "coordinator") -> str:
    try:
        return load_model_profile(role).model
    except (OSError, ValueError) as exc:
        raise OceanAgentRuntimeError(str(exc)) from exc


async def _build_runtime(
    *,
    services: OceanToolServices,
    workspace_path: Path,
    budget: OceanAgentBudget,
    operation_id_factory: Callable[[str, str, str], str],
    composition: OceanRuntimeComposition,
    thread_fallback: str,
    model_role: ModelRole,
    token_budget_wind_down: bool = False,
) -> OceanAgentRuntime:
    try:
        profile = load_model_profile(model_role)
        thread_id = services.agent_thread_id or thread_fallback
        engine, close = await build_deep_agent_engine(
            profile=profile,
            tools=composition.profile.tool_registry,
            system_prompt=composition.system_prompt,
            cwd=workspace_path,
            thread_id=thread_id,
            max_turns=budget.max_turns,
            max_input_tokens=budget.max_input_tokens if token_budget_wind_down else 0,
            max_output_tokens=budget.max_output_tokens if token_budget_wind_down else 0,
            operation_id_factory=operation_id_factory,
        )
    except Exception as exc:  # model integrations expose heterogeneous errors
        raise OceanAgentRuntimeError(
            f"Could not initialize the OceanMind Deep Agent runtime: {exc}"
        ) from exc
    return OceanAgentRuntime(
        provider_id=profile.provider,
        model_id=profile.model,
        engine=engine,
        base_system_prompt=composition.system_prompt,
        _close=close,
    )


async def build_default_ocean_agent_runtime(
    services: OceanToolServices,
    workspace_path: Path,
    budget: OceanAgentBudget,
    operation_id_factory: Callable[[str, str, str], str],
    *,
    active_profile: str | None = None,
) -> OceanAgentRuntime:
    del active_profile
    composition = await build_ocean_runtime(services=services)
    scope = services.task_id or f"workspace-{services.workspace_id}"
    return await _build_runtime(
        services=services,
        workspace_path=workspace_path,
        budget=budget,
        operation_id_factory=operation_id_factory,
        composition=composition,
        thread_fallback=f"coordinator:{scope}",
        model_role="coordinator",
    )


async def build_default_ocean_expert_runtime(
    services: OceanToolServices,
    workspace_path: Path,
    budget: OceanAgentBudget,
    operation_id_factory: Callable[[str, str, str], str],
) -> OceanAgentRuntime:
    composition = await build_ocean_expert_runtime(services=services)
    scope = services.work_order_id or services.expert_child_id or services.workspace_id
    return await _build_runtime(
        services=services,
        workspace_path=workspace_path,
        budget=budget,
        operation_id_factory=operation_id_factory,
        composition=composition,
        thread_fallback=f"expert:{scope}",
        model_role="expert",
        token_budget_wind_down=True,
    )


async def build_default_ocean_discussion_runtime(
    services: OceanToolServices,
    workspace_path: Path,
    budget: OceanAgentBudget,
    operation_id_factory: Callable[[str, str, str], str],
) -> OceanAgentRuntime:
    composition = await build_ocean_discussion_runtime(services=services)
    scope = services.work_order_id or services.expert_child_id or services.workspace_id
    return await _build_runtime(
        services=services,
        workspace_path=workspace_path,
        budget=budget,
        operation_id_factory=operation_id_factory,
        composition=composition,
        thread_fallback=f"discussion:{scope}",
        model_role="expert",
    )


__all__ = [
    "OCEAN_EXPERT_MIN_RESPONSE_TOKENS",
    "OceanAgentBudget",
    "OceanAgentRuntime",
    "OceanAgentRuntimeError",
    "OceanAgentRuntimeFactory",
    "build_default_ocean_agent_runtime",
    "build_default_ocean_discussion_runtime",
    "build_default_ocean_expert_runtime",
    "configured_model_id",
    "configured_provider_id",
]
