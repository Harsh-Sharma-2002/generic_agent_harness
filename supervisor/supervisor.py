"""Request-level Supervisor runtime for KernelAI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from observability.tracer import RuntimeTracer
from supervisor.control_tools import SupervisorControlTools
from tasks.outcome import TaskOutcome
from tasks.request import Request
from tasks.task import Task
from tasks.task_executor import TaskExecutor
from worker.clients.control import ControlToolClient
from worker.generic_worker import GenericWorker


SUPERVISOR_ROLE_PATH = (
    Path(__file__).with_name("supervisor.md")
)


class Supervisor:
    """
    Runtime responsible for exactly one KernelAI Request.

    The Supervisor owns:
    - request-level agent context,
    - Tasks created for the Request,
    - Task execution batches,
    - Task outcomes.

    Specialized work is delegated to Task Workers.
    """

    def __init__(
        self,
        request: Request,
        max_iterations: int = 10,
        max_concurrent_tasks: int = 5,
        verbose: bool = False,
        local: bool = False,
        trace: RuntimeTracer | None = None,
    ) -> None:
        self.request = request

        if max_concurrent_tasks < 1:
            raise ValueError(
                "max_concurrent_tasks must be at least 1."
            )

        self.max_iterations = max_iterations
        self.verbose = verbose
        self.local = local
        self.trace = trace

        # Every Task created during this Request.
        self.tasks: dict[str, Task] = {}

        # Tasks created during the current Supervisor planning step.
        self.pending_tasks: list[Task] = []

        # Outcomes returned by child Task Workers.
        self.outcomes: dict[str, TaskOutcome] = {}

        # Runtime responsible for executing child Tasks.
        self.task_executor = TaskExecutor(
            max_iterations=max_iterations,
            verbose=verbose,
            local=local,
            trace=self.trace,
        )

        self.task_semaphore = asyncio.Semaphore(
            max_concurrent_tasks
        )

        # Supervisor control capabilities.
        control_tools = SupervisorControlTools(
            supervisor=self,
        )

        self.control_client = ControlToolClient(
            tools=control_tools.get_tools(),
        )

        # Request-level GenericWorker.
        role_prompt = SUPERVISOR_ROLE_PATH.read_text(
            encoding="utf-8"
        )

        self.agent = GenericWorker(
            tools=self.control_client,
            system_prompt=role_prompt,
            allowed_tools={
                "list_skills",
                "delegate_task",
            },
            max_iterations=max_iterations,
            verbose=verbose,
            local=local,
            trace=self.trace,
            worker_role="supervisor",
        )

    async def _execute_task(
    self,
    task: Task,
) -> TaskOutcome:
        """
        Execute one child Task while respecting this Supervisor's
        concurrent Task limit.
        """

        self._emit(
            event="task_waiting",
            task_id=task.task_id,
        )

        async with self.task_semaphore:
            self._emit(
                event="task_started",
                task_id=task.task_id,
            )

            return await self.task_executor.execute(
            task
            )

    async def run(
        self,
    ) -> dict[str, Any]:
        """
        Execute the complete lifecycle of this Request.
        """

        self._emit(
            event="supervisor_started",
        )

        try:
            await self.agent.start(
                query=self.request.query,
                request_id=self.request.request_id,
            )

            while not self.agent.is_done:

                self._emit(
                    event="supervisor_step_started",
                )

                # One Supervisor reasoning / control-tool step.
                await self.agent.run_step()

                self._emit(
                    event="supervisor_step_completed",
                )

                if self.agent.is_done:
                    break

                # No Tasks were delegated during this step.
                if not self.pending_tasks:
                    continue

                # Detach the current batch before execution.
                #
                # delegate_task() calls during a future planning step
                # therefore belong to a new batch.
                batch = self.pending_tasks
                self.pending_tasks = []

                self._emit(
                    event="batch_started",
                    task_count=len(batch),
                )

                # Execute all Tasks from this planning point together.
                outcomes = await asyncio.gather(
                    *[
                        self._execute_task(task)
                        for task in batch
                    ]
                )

                self._emit(
                    event="batch_completed",
                    task_count=len(outcomes),
                )

                # Store outcomes.
                for outcome in outcomes:
                    self.outcomes[
                        outcome.task_id
                    ] = outcome

                # Give bounded Task outcomes back to the Supervisor.
                self._add_outcomes_to_context(
                    outcomes
                )

            if self.agent.result is None:
                raise RuntimeError(
                    "Supervisor completed without producing a result."
                )

            self._emit(
                event="supervisor_completed",
            )

            return self.agent.result

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self._emit(
                event="supervisor_failed",
                error=type(exc).__name__,
            )
            raise

    def _add_outcomes_to_context(
        self,
        outcomes: list[TaskOutcome],
    ) -> None:
        """
        Add child Task outcomes to the Supervisor's request context.

        Only bounded Task results/failures are propagated upward.
        Child Worker context and trace remain isolated.
        """

        serialized_outcomes = []

        for outcome in outcomes:
            serialized_outcomes.append(
                {
                    "task_id": outcome.task_id,
                    "status": outcome.status.value,
                    "result": outcome.result,
                    "error": outcome.error,
                }
            )

        self.agent.messages.append(
            {
                "role": "user",
                "content": (
                    "The delegated Tasks from the previous "
                    "planning step have finished.\n\n"
                    + json.dumps(
                        serialized_outcomes,
                        default=str,
                    )
                ),
            }
        )

    def _emit(
        self,
        event: str,
        task_id: str | None = None,
        **data: Any,
    ) -> None:
        """
        Emit one Supervisor lifecycle event.
        """

        if self.trace is None:
            return

        self.trace.emit(
            component="supervisor",
            event=event,
            request_id=self.request.request_id,
            task_id=task_id,
            **data,
        )