"""Generic agent worker for KernelAI."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from observability.sanitize import (
    MAX_ARGUMENTS_BYTES,
    bound_text,
    build_arguments_trace,
    build_result_trace,
    scrub_error_message,
)
from observability.tracer import RuntimeTracer
from worker.base import BaseWorker
from worker.clients.base import ToolClient
from worker.llm_caller import LLMCaller
from worker.local_llm_caller import LocalLLMCaller
from worker.message import (
    InvalidWorkerResult,
    WorkerIterationExhausted,
    WorkerTaskResult,
)


FINALIZATION_INSTRUCTION = """\
Execution for this Task is now finished. Do not call any tools.

Convert the completed attempt above into the required structured Task
result. Preserve the final answer in 'result'.

Return status 'completed' with no escalation when the assigned Task was
completed within the authority and tools you already had.

Return status 'blocked' only when completing the Task genuinely
requires authority you do not have. In that case preserve the work
completed so far and include exactly one escalation object describing
the required authority.

Return one JSON object matching this schema:

{schema}
"""


class GenericWorker(BaseWorker):
    """
    Generic KernelAI agent runtime.

    Behavior is supplied through a system prompt.
    Tools are provided through the configured tool client.
    """

    def __init__(
        self,
        tools: ToolClient,
        system_prompt: str,
        allowed_tools: set[str] | None = None,
        max_iterations: int = 10,
        verbose: bool = False,
        local: bool = False,
        worker_role: str = "worker",
        final_response_format: (
            dict[str, Any] | None
        ) = None,
        trace: RuntimeTracer | None = None,
    ) -> None:
        self.tools = tools
        self.system_prompt = system_prompt
        self.allowed_tools = allowed_tools
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.worker_role = worker_role

        # Optional structured finalization contract. When set, the
        # Worker's final free-form turn is packaged into the schema by
        # one bounded, tool-free LLM call instead of being accepted as
        # {"content": ...}. Supervisor and Learning Supervisor leave
        # this unset and keep their existing behavior.
        self.final_response_format = final_response_format

        # Existing detailed per-worker trace.
        self.trace: list[dict[str, Any]] = []

        # Shared KernelAI runtime observability trace.
        self.runtime_trace = trace

        self.messages: list[ChatCompletionMessageParam] = []

        self.available_tools: list[dict[str, Any]] = []

        self.request_id: str | None = None
        self.task_id: str | None = None

        self.iteration = 0

        self.is_done = False
        self.result: Any = None

        if local:
            self.llm = LocalLLMCaller()
        else:
            self.llm = LLMCaller()

    def _trace(
        self,
        event: str,
        data: Any = None,
    ) -> None:
        """
        Record a detailed per-worker execution event and optionally
        print it.
        """

        self.trace.append(
            {
                "event": event,
                "data": data,
            }
        )

        if not self.verbose:
            return

        print("\n" + "-" * 60)
        print(f"[{event}]")
        print("-" * 60)

        if data is None:
            return

        if isinstance(data, (dict, list)):
            print(
                json.dumps(
                    data,
                    indent=2,
                    default=str,
                )
            )
        else:
            print(data)

    def _emit(
        self,
        event: str,
        **data: Any,
    ) -> None:
        """
        Emit one structured KernelAI runtime event.
        """

        if self.runtime_trace is None:
            return

        self.runtime_trace.emit(
            component="generic_worker",
            event=event,
            request_id=self.request_id,
            task_id=self.task_id,
            worker_role=self.worker_role,
            **data,
        )

    def _bounded_error(
        self,
        exc: Exception,
        limit: int = 300,
    ) -> str:
        """
        Return a bounded error message suitable for observability.
        """

        message = str(exc)

        if len(message) <= limit:
            return message

        return message[:limit] + "..."

    def _last_draft(self) -> str:
        """
        Return the most recent assistant draft, if one exists.
        """

        for message in reversed(self.messages):
            if message.get("role") != "assistant":
                continue

            content = message.get("content")

            if (
                isinstance(content, str)
                and content.strip()
            ):
                return content

        return (
            "Execution stopped before a final answer was produced: "
            "the attempt reached its iteration budget."
        )

    def _finalization_instruction(
        self,
        *,
        exhausted: bool = False,
    ) -> str:
        """
        Render the finalization instruction.

        The schema is included so a provider that cannot honor schema
        response mode still receives the same contract.

        An exhausted attempt receives an explicit notice that the Task
        did not complete: the runtime still decides the lifecycle, and
        the notice stops the finalizer from presenting partial work as
        a finished result.
        """

        schema = "{}"

        if self.final_response_format is not None:
            json_schema = self.final_response_format.get(
                "json_schema"
            )

            if isinstance(json_schema, dict):
                schema = json.dumps(
                    json_schema.get("schema", {}),
                    indent=2,
                )
            else:
                schema = json.dumps(
                    self.final_response_format,
                    indent=2,
                )

        instruction = FINALIZATION_INSTRUCTION.format(
            schema=schema
        )

        if not exhausted:
            return instruction

        return (
            "The Worker's execution budget is exhausted. This Task "
            "was NOT completed.\n\n"
            "Do not claim the Task succeeded. Report only the work "
            "actually finished, the findings and artifacts that "
            "actually exist, and what remains to be done. The runtime "
            "records this attempt as failed regardless of the status "
            "you return.\n\n"
            + instruction
        )

    async def _finalize_result(
        self,
        draft: str,
        *,
        exhausted: bool = False,
    ) -> WorkerTaskResult:
        """
        Package the completed attempt into the canonical result.

        Exactly one tool-free LLM call is made with the configured
        response format. Finalization never performs more execution
        work and never exposes execution tools.
        """

        if self.final_response_format is None:
            raise RuntimeError(
                "Worker finalization requires a final response "
                "format."
            )

        self._emit(
            event="worker_finalization_started",
            iteration=self.iteration,
            exhausted=exhausted,
        )

        messages: list[ChatCompletionMessageParam] = [
            *self.messages,
            {
                "role": "assistant",
                "content": draft,
            },
            {
                "role": "user",
                "content": self._finalization_instruction(
                    exhausted=exhausted
                ),
            },
        ]

        try:
            response = await self.llm.call(
                messages=messages,
                tools=None,
                response_format=(
                    self.final_response_format
                ),
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self._emit(
                event="worker_finalization_failed",
                iteration=self.iteration,
                error=type(exc).__name__,
            )

            raise InvalidWorkerResult(
                "Structured Worker finalization failed: "
                f"{exc}"
            ) from exc

        try:
            result = WorkerTaskResult.from_payload(
                response.content
            )

        except InvalidWorkerResult:
            self._emit(
                event="worker_finalization_failed",
                iteration=self.iteration,
                error="InvalidWorkerResult",
            )
            raise

        self._trace(
            "FINALIZATION",
            result.model_dump(mode="json"),
        )

        self._emit(
            event="worker_finalization_completed",
            iteration=self.iteration,
            exhausted=exhausted,
            status=result.status,
            escalation=(
                result.escalation.kind.value
                if result.escalation is not None
                else None
            ),
        )

        return result

    async def start(
        self,
        query: str,
        request_id: str,
        task_id: str | None = None,
    ) -> None:
        """
        Initialize one GenericWorker execution.
        """

        if self.request_id is not None:
            raise RuntimeError(
                "GenericWorker has already been initialized."
            )

        self.request_id = request_id
        self.task_id = task_id

        self.messages = [
            {
                "role": "system",
                "content": self.system_prompt,
            },
            {
                "role": "user",
                "content": query,
            },
        ]

        self._trace(
            "REQUEST",
            {
                "request_id": request_id,
                "query": query,
            },
        )

        available_tools = await self.tools.get_tools()

        if self.allowed_tools is not None:
            available_tools = [
                tool
                for tool in available_tools
                if tool["function"]["name"] in self.allowed_tools
            ]

        self.available_tools = available_tools

        self._trace(
            "AVAILABLE TOOLS",
            [
                tool["function"]["name"]
                for tool in self.available_tools
            ],
        )

        self._emit(
            event="worker_started",
        )

    async def run_step(
        self,
    ) -> bool:
        """
        Execute exactly one agent iteration.

        Returns True when the worker has produced its final result.
        Returns False when additional execution is required.
        """

        if self.request_id is None:
            raise RuntimeError(
                "GenericWorker must be initialized before run_step()."
            )

        if self.is_done:
            return True

        if self.iteration >= self.max_iterations:
            # The execution budget is exhausted: no further execution
            # is possible. A Worker with a finalization contract still
            # packages its partial work, but the runtime — not the
            # finalizer — decides that the attempt failed.
            if self.final_response_format is not None:

                self._emit(
                    event="worker_iteration_limit_reached",
                    iteration=self.iteration,
                    max_iterations=self.max_iterations,
                )

                try:
                    partial = await self._finalize_result(
                        self._last_draft(),
                        exhausted=True,
                    )

                except InvalidWorkerResult as exc:
                    self._emit(
                        event="worker_iteration_exhausted",
                        iteration=self.iteration,
                        error="InvalidWorkerResult",
                    )

                    raise WorkerIterationExhausted(
                        None,
                        "Worker iteration budget exhausted and "
                        f"partial packaging failed: {exc}",
                    ) from exc

                self._emit(
                    event="worker_iteration_exhausted",
                    iteration=self.iteration,
                    packaged_status=partial.status,
                )

                raise WorkerIterationExhausted(partial)

            raise RuntimeError(
                "Worker exceeded maximum iterations "
                f"({self.max_iterations}) "
                f"for request {self.request_id!r}."
            )

        self.iteration += 1

        self._emit(
            event="worker_step_started",
            iteration=self.iteration,
        )

        self._trace(
            f"ITERATION {self.iteration} - LLM CALL"
        )

        self._emit(
            event="llm_call_started",
            iteration=self.iteration,
        )

        try:
            response = await self.llm.call(
                messages=self.messages,
                tools=self.available_tools,
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self._emit(
                event="llm_call_failed",
                iteration=self.iteration,
                error=type(exc).__name__,
            )
            raise

        self._emit(
            event="llm_call_completed",
            iteration=self.iteration,
        )

        if response.content:
            self._trace(
                "ASSISTANT",
                response.content,
            )

        # Final response.

        if not response.tool_calls:
            if response.content and response.content.strip():

                # Finalization boundary. A Task Worker configured with
                # a final response contract packages the completed
                # attempt into the canonical result through one
                # bounded, tool-free LLM call.
                if self.final_response_format is not None:
                    self.result = await self._finalize_result(
                        response.content
                    )

                    self.is_done = True

                    self._emit(
                        event="worker_step_completed",
                        iteration=self.iteration,
                    )

                    self._emit(
                        event="worker_completed",
                    )

                    return True

                self.result = {
                    "content": response.content,
                }

                self.is_done = True

                self._trace(
                    "FINAL RESPONSE",
                    response.content,
                )

                self._emit(
                    event="worker_step_completed",
                    iteration=self.iteration,
                )

                self._emit(
                    event="worker_completed",
                )

                return True

            raise RuntimeError(
                "LLM returned neither tool calls nor final content "
                f"for request {self.request_id!r}."
            )

        # Preserve assistant tool-call message.

        assistant_message: ChatCompletionMessageParam = {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in response.tool_calls
            ],
        }

        self.messages.append(
            assistant_message
        )

        # Execute every tool requested during this turn.

        for tool_call in response.tool_calls:
            tool_call_id = tool_call.id
            tool_name = tool_call.function.name

            started_at = time.perf_counter()

            # Dispatch authorization.
            #
            # Schema filtering is a presentation choice, not a
            # security boundary: the model can emit any tool name, so
            # every model-originated call is checked against this
            # Worker's configured authority immediately before it
            # reaches the ToolClient. Arguments are not traced for a
            # rejected call.
            if (
                self.allowed_tools is not None
                and tool_name not in self.allowed_tools
            ):
                unauthorized_message = (
                    f"Tool {tool_name!r} is not authorized for "
                    "this Worker."
                )

                self._emit(
                    event="tool_call_started",
                    iteration=self.iteration,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                )

                self._emit(
                    event="tool_call_failed",
                    iteration=self.iteration,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    duration_seconds=round(
                        time.perf_counter()
                        - started_at,
                        6,
                    ),
                    error="unauthorized_tool",
                    error_message=unauthorized_message,
                )

                tool_result = json.dumps(
                    {
                        "ok": False,
                        "error": unauthorized_message,
                    }
                )

                self._trace(
                    "TOOL RESULT",
                    tool_result,
                )

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    }
                )

                continue

            secret_values: list[str] = []

            try:
                arguments = json.loads(
                    tool_call.function.arguments
                )

                arguments_trace, secret_values = (
                    build_arguments_trace(
                        arguments
                    )
                )

                self._trace(
                    "TOOL CALL",
                    {
                        "tool_call_id": tool_call_id,
                        "tool": tool_name,
                        **arguments_trace,
                    },
                )

                self._emit(
                    event="tool_call_started",
                    iteration=self.iteration,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    **arguments_trace,
                )

                tool_result = await self.tools.call_tool(
                    name=tool_name,
                    arguments=arguments,
                )

                self._emit(
                    event="tool_call_completed",
                    iteration=self.iteration,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    duration_seconds=round(
                        time.perf_counter()
                        - started_at,
                        6,
                    ),
                    **build_result_trace(
                        tool_result
                    ),
                )

            except json.JSONDecodeError as exc:
                self._emit(
                    event="tool_call_started",
                    iteration=self.iteration,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments_raw=bound_text(
                        tool_call.function.arguments,
                        MAX_ARGUMENTS_BYTES,
                    ),
                    arguments_parse_error=(
                        f"{type(exc).__name__}: "
                        f"{exc.msg}"
                    ),
                )

                self._emit(
                    event="tool_call_failed",
                    iteration=self.iteration,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    duration_seconds=round(
                        time.perf_counter()
                        - started_at,
                        6,
                    ),
                    error=type(exc).__name__,
                    error_message=self._bounded_error(
                        exc
                    ),
                )

                tool_result = json.dumps(
                    {
                        "ok": False,
                        "error": (
                            "Invalid JSON tool arguments: "
                            f"{exc.msg}"
                        ),
                    }
                )

            except (ValueError, RuntimeError) as exc:
                self._emit(
                    event="tool_call_failed",
                    iteration=self.iteration,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    duration_seconds=round(
                        time.perf_counter()
                        - started_at,
                        6,
                    ),
                    error=type(exc).__name__,
                    error_message=scrub_error_message(
                        self._bounded_error(exc),
                        secret_values,
                    ),
                )

                tool_result = json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                    }
                )

            self._trace(
                "TOOL RESULT",
                tool_result,
            )

            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

        self._trace(
            f"ITERATION {self.iteration} COMPLETE"
        )

        self._emit(
            event="worker_step_completed",
            iteration=self.iteration,
        )

        return False

    async def run(
        self,
        query: str,
        request_id: str,
        task_id: str | None = None,
    ) -> Any:
        """
        Execute continuously until the worker produces a final result.
        """

        await self.start(
            query=query,
            request_id=request_id,
            task_id=task_id,
        )

        try:
            while not self.is_done:
                await self.run_step()

            if self.result is None:
                raise RuntimeError(
                    "Worker completed without producing a result."
                )

            return self.result

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self._emit(
                event="worker_failed",
                error=type(exc).__name__,
            )
            raise