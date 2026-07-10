"""Tool for creating background tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tasks.manager import get_task_manager
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskCreateToolInput(BaseModel):
    """Arguments for task creation."""

    type: str = Field(default="local_bash", description="Task type: local_bash")
    description: str = Field(description="Short task description")
    command: str = Field(description="Shell command to run in the background")


class TaskCreateTool(BaseTool):
    """Create a background task."""

    name = "task_create"
    description = "Create a background shell task."
    input_model = TaskCreateToolInput

    async def execute(self, arguments: TaskCreateToolInput, context: ToolExecutionContext) -> ToolResult:
        manager = get_task_manager()
        if arguments.type != "local_bash":
            return ToolResult(output=f"unsupported task type: {arguments.type}", is_error=True)

        task = await manager.create_shell_task(
            command=arguments.command,
            description=arguments.description,
            cwd=context.cwd,
        )
        return ToolResult(output=f"Created task {task.id} ({task.type})")
