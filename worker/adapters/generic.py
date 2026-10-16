"""KernelAI GenericWorker agent adapter."""

from __future__ import annotations

from observability.tracer import RuntimeTracer
from worker.base import BaseWorker
from worker.clients.base import ToolClient
from worker.generic_worker import GenericWorker


def create_generic_worker(
    *,
    tools: ToolClient,
    system_prompt: str,
    allowed_tools: set[str] | None = None,
    max_iterations: int = 10,
    verbose: bool = False,
    local: bool = False,
    worker_role: str = "worker",
    trace: RuntimeTracer | None = None,
) -> BaseWorker:
    """
    Create KernelAI's built-in GenericWorker agent.

    This adapter isolates the concrete GenericWorker implementation
    from the rest of the KernelAI runtime.
    """

    return GenericWorker(
        tools=tools,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        max_iterations=max_iterations,
        verbose=verbose,
        local=local,
        worker_role=worker_role,
        trace=trace,
    )

