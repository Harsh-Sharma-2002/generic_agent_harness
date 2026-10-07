"""Request-level Supervisor runtime for KernelAI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

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
        verbose: bool = False,
        local: bool = False,
    ) -> None:
        self.request = request

        self.max_iterations = max_iterations
        self.verbose = verbose
        self.local = local

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
        )

        # ---------------------------------------------------------
        # Supervisor control capabilities
        # ---------------------------------------------------------

        control_tools = SupervisorControlTools(
            supervisor=self,
        )

        self.control_client = ControlToolClient(
            tools=control_tools.get_tools(),
        )

        # ---------------------------------------------------------
        # Request-level GenericWorker
        # ---------------------------------------------------------

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
        )

    async def run(
        self,
    ) -> dict[str, Any]:
        """
        Execute the complete lifecycle of this Request.
        """

        await self.agent.start(
            query=self.request.query,
            request_id=self.request.request_id,
        )

        while not self.agent.is_done:

            # -----------------------------------------------------
            # One Supervisor reasoning / control-tool step
            # -----------------------------------------------------

            await self.agent.run_step()

            if self.agent.is_done:
                break

            # -----------------------------------------------------
            # No Tasks were delegated during this step.
            # -----------------------------------------------------

            if not self.pending_tasks:
                continue

            # -----------------------------------------------------
            # Detach the current batch before execution.
            #
            # delegate_task() calls during a future planning step
            # therefore belong to a new batch.
            # -----------------------------------------------------

            batch = self.pending_tasks
            self.pending_tasks = []

            # -----------------------------------------------------
            # Execute all Tasks from this planning point together.
            # -----------------------------------------------------

            outcomes = await asyncio.gather(
                *[
                    self.task_executor.execute(task)
                    for task in batch
                ]
            )

            # -----------------------------------------------------
            # Store outcomes.
            # -----------------------------------------------------

            for outcome in outcomes:
                self.outcomes[
                    outcome.task_id
                ] = outcome

            # -----------------------------------------------------
            # Give bounded Task outcomes back to the Supervisor.
            # -----------------------------------------------------

            self._add_outcomes_to_context(
                outcomes
            )

        if self.agent.result is None:
            raise RuntimeError(
                "Supervisor completed without producing a result."
            )

        return self.agent.result

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