"""OceanMind tool contracts and LangChain adapters.

Domain tools remain ordinary Python objects. This module is the only place
that knows how LangChain invokes a tool, keeping scientific services independent
from Deep Agents and LangGraph integration details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool as LangChainBaseTool
from langchain_core.tools import InjectedToolCallId, StructuredTool, ToolException
from pydantic import BaseModel, create_model


@dataclass
class ToolExecutionContext:
    cwd: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True)
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    MUTATION = "mutation"
    EXTERNAL_IO = "external_io"


class BaseTool(ABC):
    name: str
    description: str
    input_model: type[BaseModel]

    @abstractmethod
    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        raise NotImplementedError

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    def effect_for(self, arguments: BaseModel) -> ToolEffect:
        return ToolEffect.READ_ONLY if self.is_read_only(arguments) else ToolEffect.MUTATION

    def concurrency_key(self, arguments: BaseModel) -> str | None:
        del arguments
        return None

    def input_validation_error(
        self, raw_input: dict[str, object], error: Exception
    ) -> ToolResult | None:
        del raw_input, error
        return None

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def to_api_schema(self) -> list[dict[str, Any]]:
        return [tool.to_api_schema() for tool in self._tools.values()]

    def as_langchain_tools(
        self,
        *,
        cwd: Path,
        operation_id_factory: Any,
    ) -> list[LangChainBaseTool]:
        return [
            _adapt_tool(tool, cwd=cwd, operation_id_factory=operation_id_factory)
            for tool in self.list_tools()
        ]


def _adapt_tool(
    tool: BaseTool,
    *,
    cwd: Path,
    operation_id_factory: Any,
) -> LangChainBaseTool:
    async def invoke(config: RunnableConfig, **raw: Any) -> str:
        tool_call_id = str(raw.pop("tool_call_id"))
        arguments = tool.input_model.model_validate(raw)
        configurable = dict(config.get("configurable", {}))
        metadata = dict(config.get("metadata", {}))
        request_id = str(configurable.get("request_id") or configurable.get("thread_id") or "")
        turn_id = str(metadata.get("langgraph_step") or configurable.get("checkpoint_id") or "0")
        operation_id = operation_id_factory(request_id, "langgraph", tool_call_id)
        result = await tool.execute(
            arguments,
            ToolExecutionContext(
                cwd=cwd,
                metadata=metadata,
                request_id=request_id or None,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                operation_id=operation_id,
            ),
        )
        if result.is_error:
            raise ToolException(result.output)
        return result.output

    invoke.__name__ = f"invoke_{tool.name}"
    invoke.__doc__ = tool.description
    adapter_schema = create_model(
        f"{tool.input_model.__name__}WithCallId",
        tool_call_id=(Annotated[str, InjectedToolCallId], ...),
        __base__=tool.input_model,
    )
    return StructuredTool.from_function(
        coroutine=invoke,
        name=tool.name,
        description=tool.description,
        args_schema=adapter_schema,
        handle_tool_error=True,
    )


__all__ = [
    "BaseTool",
    "ToolEffect",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
]
