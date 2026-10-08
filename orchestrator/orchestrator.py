"""Root request orchestrator for KernelAI."""

from __future__ import annotations

import asyncio
from typing import Any

from supervisor.supervisor import Supervisor
from tasks.request import Request


class Orchestrator:
    """
    Root runtime for KernelAI.

    The Orchestrator owns system-level Request lifecycle and
    admission control.

    It does not reason about Requests, create Tasks, select skills,
    or execute tools.

    Each admitted Request is assigned exactly one Supervisor.
    """

    def __init__(
        self,
        supervisor_max_iterations: int = 10,
        max_concurrent_requests: int = 5,
        max_concurrent_tasks: int = 5,
        verbose: bool = False,
        local: bool = False,
    ) -> None:
        if max_concurrent_requests < 1:
            raise ValueError(
                "max_concurrent_requests must be at least 1."
            )

        if max_concurrent_tasks < 1:
            raise ValueError(
                "max_concurrent_tasks must be at least 1."
            )

        self.supervisor_max_iterations = (
            supervisor_max_iterations
        )

        self.max_concurrent_requests = (
            max_concurrent_requests
        )

        self.max_concurrent_tasks = (
            max_concurrent_tasks
        )

        self.verbose = verbose
        self.local = local

        # Every Request currently inside KernelAI.
        #
        # This includes Requests waiting for admission and
        # Requests currently executing.
        #
        # Request IDs must be unique while they are in the system.
        self.submitted_request_ids: set[str] = set()

        # Requests currently admitted and executing.
        #
        # Each active Request owns exactly one Supervisor.
        self.active_requests: dict[
            str,
            Supervisor,
        ] = {}

        # Base Request admission-control mechanism.
        #
        # At most max_concurrent_requests Supervisors may execute
        # concurrently.
        #
        # Future resource-aware admission control can replace this
        # mechanism without changing Supervisor behavior.
        self.request_semaphore = asyncio.Semaphore(
            self.max_concurrent_requests
        )

    async def submit(
        self,
        request: Request,
    ) -> dict[str, Any]:
        """
        Submit one Request to KernelAI.

        The Request ID is reserved immediately so another Request
        with the same ID cannot enter the system while this Request
        is waiting or executing.

        The Request then waits until admission capacity becomes
        available.

        Once admitted, it receives its own Supervisor and executes
        independently.
        """

        request_id = request.request_id

        # Reserve the Request ID before the first await so waiting
        # Requests also own their IDs.
        if request_id in self.submitted_request_ids:
            raise ValueError(
                "Request ID "
                f"{request_id!r} "
                "is already in the system."
            )

        self.submitted_request_ids.add(
            request_id
        )

        try:
            # Wait for Request-level admission capacity.
            async with self.request_semaphore:
                return await self._execute_request(
                    request
                )

        finally:
            # The Request has completely left the system.
            # Its ID may now be reused.
            self.submitted_request_ids.discard(
                request_id
            )

    async def _execute_request(
        self,
        request: Request,
    ) -> dict[str, Any]:
        """
        Create and execute the Supervisor for one admitted Request.
        """

        supervisor = Supervisor(
            request=request,
            max_iterations=(
                self.supervisor_max_iterations
            ),
            max_concurrent_tasks=(
                self.max_concurrent_tasks
            ),
            verbose=self.verbose,
            local=self.local,
        )

        # The Request is now active.
        self.active_requests[
            request.request_id
        ] = supervisor

        try:
            return await supervisor.run()

        finally:
            # The Supervisor is no longer executing.
            self.active_requests.pop(
                request.request_id,
                None,
            )