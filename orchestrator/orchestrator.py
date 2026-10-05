"""KernelAI orchestration runtime."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from orchestrator.events import (
    TaskCompleted,
    TaskEvent,
    TaskFailed,
)
from tasks.request import Request
from tasks.task import Task, TaskStatus


AGENT_PATH = Path(__file__).with_name("Agent.md")


class Orchestrator:
    """
    Long-lived KernelAI orchestration runtime.

    Responsibilities:
    - accept incoming user requests,
    - maintain request and task queues,
    - create delegated worker tasks,
    - track task lifecycle state,
    - receive task completion/failure events.

    Model-driven orchestration behavior is added on top of this
    runtime through the orchestration agent and its control tools.
    """

    def __init__(self) -> None:


        # Frontend/API -> Orchestrator
        #
        # Contains complete user-level requests that have not yet
        # been processed by the orchestration agent.
        self.request_queue: asyncio.Queue[Request] = (
            asyncio.Queue()
        )

        # Orchestrator -> Worker runtime
        #
        # Contains runnable tasks waiting for worker capacity.
        self.task_queue: asyncio.Queue[Task] = (
            asyncio.Queue()
        )

        # Worker runtime -> Orchestrator
        #
        # Contains notifications that delegated work has completed
        # or failed.
        self.task_events: asyncio.Queue[TaskEvent] = (
            asyncio.Queue()
        )

       
        # Runtime state
     

        # Authoritative task registry.
        #
        # task_queue contains only runnable tasks waiting for
        # execution. This registry retains each Task throughout
        # its lifecycle.
        self.tasks: dict[str, Task] = {}

        # Request -> Task relationship.
        #
        # One request may produce multiple independently
        # schedulable tasks.
        self.request_tasks: dict[str, list[str]] = {}

        # ---------------------------------------------------------
        # Orchestration agent configuration
        # ---------------------------------------------------------

        self.agent_prompt = AGENT_PATH.read_text(
            encoding="utf-8"
        )

    async def submit(
        self,
        query: str,
    ) -> str:
        """
        Submit a new user request to KernelAI.

        `query` is the original user-level request.

        The request is placed onto the request queue and this
        method returns immediately with its request ID.
        """

        query = query.strip()

        if not query:
            raise ValueError(
                "Request query cannot be empty."
            )

        request_id = (
            f"req_{uuid.uuid4().hex}"
        )

        request = Request(
            request_id=request_id,
            query=query,
        )

        # Initialize request bookkeeping before making the request
        # visible to the orchestration runtime.
        self.request_tasks[
            request_id
        ] = []

        await self.request_queue.put(
            request
        )

        return request_id

    async def create_task(
        self,
        request_id: str,
        task_query: str,
        skill_names: list[str],
    ) -> Task:
        """
        Create one independently schedulable worker task.

        `task_query` is the worker-specific job produced by the
        orchestration agent. It is intentionally distinct from
        the original Request.query.

        A task may contain multiple skills when those capabilities
        belong to the same schedulable unit of work.
        """

        if request_id not in self.request_tasks:
            raise ValueError(
                f"Unknown request {request_id!r}."
            )

        task_query = task_query.strip()

        if not task_query:
            raise ValueError(
                "Task query cannot be empty."
            )

        if not skill_names:
            raise ValueError(
                "Task must contain at least one skill."
            )

        normalized_skills: list[str] = []

        for skill_name in skill_names:
            skill_name = skill_name.strip()

            if not skill_name:
                raise ValueError(
                    "Skill names cannot be empty."
                )

            if skill_name not in normalized_skills:
                normalized_skills.append(skill_name)

        task_id = f"task_{uuid.uuid4().hex}"

        task = Task(
            request_id=request_id,
            task_id=task_id,
            query=task_query,
            skill_names=normalized_skills,
        )

        self.tasks[task_id] = task
        self.request_tasks[request_id].append(task_id)

        await self.task_queue.put(task)

        return task

    async def emit_task_completed(
        self,
        task: Task,
        result: dict[str, Any],
    ) -> None:
        """
        Complete a task and notify the orchestration runtime.

        The Task object remains in the task registry after
        completion so its lifecycle can still be inspected.
        """

        registered_task = self._get_task(
            task.task_id
        )

        if registered_task is not task:
            raise ValueError(
                f"Task object for {task.task_id!r} does not "
                "match the registered task."
            )

        if task.status == TaskStatus.COMPLETED:
            raise ValueError(
                f"Task {task.task_id!r} is already completed."
            )

        if task.status == TaskStatus.FAILED:
            raise ValueError(
                f"Task {task.task_id!r} has already failed."
            )

        task.status = TaskStatus.COMPLETED
        task.result = result
        task.error = None

        event = TaskCompleted(
            request_id=task.request_id,
            task_id=task.task_id,
            result=result,
        )

        await self.task_events.put(
            event
        )

    async def emit_task_failed(
        self,
        task: Task,
        error: str,
    ) -> None:
        """
        Fail a task and notify the orchestration runtime.
        """

        registered_task = self._get_task(
            task.task_id
        )

        if registered_task is not task:
            raise ValueError(
                f"Task object for {task.task_id!r} does not "
                "match the registered task."
            )

        if task.status == TaskStatus.COMPLETED:
            raise ValueError(
                f"Task {task.task_id!r} is already completed."
            )

        if task.status == TaskStatus.FAILED:
            raise ValueError(
                f"Task {task.task_id!r} has already failed."
            )

        error = error.strip()

        if not error:
            raise ValueError(
                "Task failure error cannot be empty."
            )

        task.status = TaskStatus.FAILED
        task.result = None
        task.error = error

        event = TaskFailed(
            request_id=task.request_id,
            task_id=task.task_id,
            error=error,
        )

        await self.task_events.put(
            event
        )

    def _get_task(
        self,
        task_id: str,
    ) -> Task:
        """
        Return a registered task by ID.
        """

        try:
            return self.tasks[
                task_id
            ]

        except KeyError as exc:
            raise ValueError(
                f"Unknown task {task_id!r}."
            ) from exc