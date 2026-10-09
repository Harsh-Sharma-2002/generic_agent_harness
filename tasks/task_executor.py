"""Execute KernelAI tasks using GenericWorker instances."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from observability.tracer import RuntimeTracer
from skills.registry import get_skill
from tasks.outcome import TaskOutcome
from tasks.task import Task, TaskStatus
from worker.clients.mcp import MCPToolClient
from worker.generic_worker import GenericWorker


TASK_WORKER_ROLE_PATH = (
    Path(__file__).with_name("task_worker.md")
)


class TaskExecutor:
    """
    Translate a KernelAI Task into a configured GenericWorker
    and execute it.

    TaskExecutor owns the boundary between task definitions and
    task-level agent execution. It does not perform scheduling or
    request-level orchestration.
    """

    def __init__(
        self,
        max_iterations: int = 10,
        verbose: bool = False,
        local: bool = False,
        trace: RuntimeTracer | None = None,
    ) -> None:
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.local = local
        self.trace = trace

        self.task_worker_role = TASK_WORKER_ROLE_PATH.read_text(
            encoding="utf-8"
        )

    async def execute(
        self,
        task: Task,
    ) -> TaskOutcome:
        """
        Execute one queued Task and return its immutable outcome.
        """

        if task.status != TaskStatus.QUEUED:
            raise ValueError(
                f"Task {task.task_id!r} cannot be executed "
                f"from status {task.status.value!r}."
            )

        if not task.skill_names:
            raise ValueError(
                f"Task {task.task_id!r} has no assigned skills."
            )

        skills = [
            get_skill(skill_name)
            for skill_name in task.skill_names
        ]

        # Build worker instructions.
        #
        # Role:
        #   Defines what this GenericWorker is responsible for.
        #
        # Skills:
        #   Define the specialized behavior needed for this Task.

        skill_prompts = [
            skill.load()
            for skill in skills
        ]

        system_prompt = "\n\n".join(
            [
                self.task_worker_role,
                *skill_prompts,
            ]
        )

        # Build capability boundary.
        #
        # A multi-skill Task receives the union of the tools granted
        # by all of its selected skills.

        allowed_tools: set[str] = set()

        for skill in skills:
            allowed_tools.update(
                skill.allowed_tools
            )

        # Create isolated Task Worker.

        worker = GenericWorker(
            tools=MCPToolClient(),
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            max_iterations=self.max_iterations,
            verbose=self.verbose,
            local=self.local,
            trace=self.trace,
            worker_role="task",
        )

        task.status = TaskStatus.RUNNING

        try:
            result = await worker.run(
                query=task.query,
                request_id=task.request_id,
                task_id=task.task_id,
            )

        except (ValueError, RuntimeError) as exc:
            task.status = TaskStatus.FAILED

            outcome = TaskOutcome(
                request_id=task.request_id,
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                result=None,
                error=str(exc),
            )

            self._emit(
                event="task_failed",
                task=task,
                error=type(exc).__name__,
            )

            return outcome

        task.status = TaskStatus.COMPLETED

        outcome = TaskOutcome(
            request_id=task.request_id,
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            result=result,
            error=None,
        )

        self._emit(
            event="task_completed",
            task=task,
        )

        return outcome

    def _emit(
        self,
        event: str,
        task: Task,
        **data: Any,
    ) -> None:
        """
        Emit one TaskExecutor lifecycle event.
        """

        if self.trace is None:
            return

        self.trace.emit(
            component="task_executor",
            event=event,
            request_id=task.request_id,
            task_id=task.task_id,
            **data,
        )