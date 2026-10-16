"""Configurable agent factory for KernelAI."""

from __future__ import annotations

import os

from observability.tracer import RuntimeTracer
from worker.base import BaseWorker
from worker.clients.base import ToolClient


AGENT_ENV = "KERNELAI_AGENT"
DEFAULT_AGENT = "generic"


def create_agent(
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
    Create the configured KernelAI agent implementation.

    Agent implementations are loaded behind adapters so runtime
    components do not depend on concrete agent classes.
    """

    backend = os.environ.get(
        AGENT_ENV,
        DEFAULT_AGENT,
    ).strip().lower()

    if backend == "generic":
        from worker.adapters.generic import create_generic_worker

        return create_generic_worker(
            tools=tools,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            max_iterations=max_iterations,
            verbose=verbose,
            local=local,
            worker_role=worker_role,
            trace=trace,
        )

    raise ValueError(
        f"Unknown KernelAI agent backend: {backend!r}. "
        "Install or implement an adapter for this backend."
    )