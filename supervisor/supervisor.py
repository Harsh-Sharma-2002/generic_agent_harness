"""Request-level Supervisor runtime for KernelAI."""

from __future__ import annotations

from pathlib import Path

from tasks.outcome import TaskOutcome
from tasks.request import Request
from tasks.task import Task
from tasks.task_executor import TaskExecutor
from worker.clients.control import ControlToolClient
from worker.generic_worker import GenericWorker


SUPERVISOR_ROLE_PATH = (
    Path(__file__).with_name("supervisor.md")
)


"""Request-level Supervisor runtime for KernelAI."""

from __future__ import annotations

from pathlib import Path

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

    The Supervisor owns the request-level agent context and the
    lifecycle of Tasks delegated for that Request.

    Specialized work is executed by Task Workers through TaskExecutor.
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

        # Every Task created for this Request.
        self.tasks: dict[str, Task] = {}

        # Tasks delegated during the current planning point.
        #
        # Control tools add Tasks here. They are not executed
        # immediately so the Supervisor can finish creating the
        # complete independent batch first.
        self.pending_tasks: list[Task] = []

        # Outcomes produced by completed or failed Task Workers.
        self.outcomes: dict[str, TaskOutcome] = {}

        # Runtime used to execute child Tasks.
        self.task_executor = TaskExecutor(
            max_iterations=max_iterations,
            verbose=verbose,
            local=local,
        )

        # Supervisor role instructions.
        self.role_prompt = SUPERVISOR_ROLE_PATH.read_text(
            encoding="utf-8"
        )

        # Constructed after Supervisor control tools are created.
        self.control_client: ControlToolClient | None = None

        # GenericWorker providing the Supervisor's agent loop.
        #
        # This worker owns the request-level messages and trace.
        self.agent: GenericWorker | None = None