"""Worker-facing lazy skill runtime.

``get_skill`` is runtime control infrastructure, not execution
authority. It is composed onto a Worker attempt's tool client and never
comes from Trusted Domain Grants.

Loading a methodology changes how the Worker approaches the Task. It
never adds a domain or a tool.
"""

from __future__ import annotations

import json
from typing import Any

from observability.tracer import RuntimeTracer
from second_brain.models import LoadedSkill, SecondBrainError
from second_brain.runtime import SecondBrainRuntime
from tasks.models import TaskState
from worker.clients.base import ToolClient


GET_SKILL_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_skill",
        "description": (
            "Load one methodology from an authorized domain. Pass "
            "the canonical skill ID shown in the supplied DOMAIN "
            "documents, for example 'coding.systematic-debugging'. "
            "Loading a skill does not grant domains or tools. "
            "Loading a skill that is already loaded returns it again "
            "without consuming another skill slot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": (
                        "Canonical skill ID: "
                        "'<domain>.<skill-name>'."
                    ),
                },
            },
            "required": [
                "skill_id",
            ],
            "additionalProperties": False,
        },
    },
}


SKILL_LIMIT_REACHED = "skill_limit_reached"


class WorkerSkillRuntime:
    """
    Deterministic lazy skill loading for one Worker attempt.

    Owns the only mutation of ``TaskState.loaded_skills``: a successful
    ``SecondBrainRuntime`` resolution appends its canonical skill ID.
    Failed, unauthorized, malformed, or over-limit requests change no
    state.
    """

    def __init__(
        self,
        *,
        second_brain: SecondBrainRuntime,
        task_state: TaskState,
        attempt: int,
        request_id: str,
        task_id: str,
        trace: RuntimeTracer | None = None,
    ) -> None:
        self.second_brain = second_brain
        self.task_state = task_state
        self.attempt = attempt
        self.request_id = request_id
        self.task_id = task_id
        self.trace = trace

    def load_skill(
        self,
        skill_id: object,
    ) -> dict[str, Any]:
        """
        Resolve, authorize, and account for one methodology request.
        """

        if (
            not isinstance(skill_id, str)
            or not skill_id.strip()
        ):
            return self._reject(
                error="invalid_skill_id",
                message=(
                    "get_skill requires a non-empty 'skill_id'."
                ),
                skill_id=None,
                domain=None,
            )

        requested = skill_id.strip()
        domain_hint = self._domain_hint(requested)

        self._emit(
            "skill_requested",
            skill_id=requested,
            domain=domain_hint,
        )

        # SecondBrainRuntime remains authoritative for canonical ID
        # resolution, domain authorization, file resolution, metadata
        # validation, and projection creation.
        loaded = self.second_brain.get_skill(
            requested,
            self.task_state.authorized_domains,
        )

        if isinstance(
            loaded,
            SecondBrainError,
        ):
            self._emit(
                "skill_rejected",
                skill_id=requested,
                domain=domain_hint,
                error=loaded.code.value,
            )

            return self._reject(
                error=loaded.code.value,
                message=loaded.message,
                skill_id=requested,
                domain=domain_hint,
            )

        canonical_id = loaded.metadata.skill_id
        domain = loaded.metadata.domain

        # Already loaded: return the projection again without
        # consuming another slot. Accounting is by canonical ID.
        if canonical_id in self.task_state.loaded_skills:
            self._emit(
                "skill_reused",
                skill_id=canonical_id,
                domain=domain,
            )

            return self._loaded_payload(
                loaded,
                reused=True,
            )

        if (
            len(self.task_state.loaded_skills)
            >= self.task_state.skill_limit
        ):
            self._emit(
                "skill_limit_reached",
                skill_id=canonical_id,
                domain=domain,
            )

            return self._reject(
                error=SKILL_LIMIT_REACHED,
                message=(
                    "Skill limit reached "
                    f"({len(self.task_state.loaded_skills)} of "
                    f"{self.task_state.skill_limit}). Another "
                    "methodology cannot be loaded until the "
                    "Supervisor grants more capacity."
                ),
                skill_id=canonical_id,
                domain=domain,
            )

        self.task_state.loaded_skills.append(
            canonical_id
        )

        self._emit(
            "skill_loaded",
            skill_id=canonical_id,
            domain=domain,
        )

        return self._loaded_payload(
            loaded,
            reused=False,
        )

    def _loaded_payload(
        self,
        loaded: LoadedSkill,
        *,
        reused: bool,
    ) -> dict[str, Any]:
        """
        Return only the model-facing projection and bookkeeping.
        """

        projection = loaded.projection

        return {
            "ok": True,
            "reused": reused,
            "skill": {
                "skill_id": loaded.metadata.skill_id,
                "name": projection.name,
                "description": projection.description,
                "content": projection.content,
                "related": [
                    {
                        "skill_id": related.skill_id,
                        "description": related.description,
                    }
                    for related in projection.related
                ],
            },
            "loaded_skills": len(
                self.task_state.loaded_skills
            ),
            "skill_limit": self.task_state.skill_limit,
        }

    def _reject(
        self,
        *,
        error: str,
        message: str,
        skill_id: str | None,
        domain: str | None,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "error": error,
            "message": message,
            "skill_id": skill_id,
            "domain": domain,
            "loaded_skills": len(
                self.task_state.loaded_skills
            ),
            "skill_limit": self.task_state.skill_limit,
        }

    @staticmethod
    def _domain_hint(
        skill_id: str,
    ) -> str | None:
        """
        Report the domain segment of a requested ID.

        This is reporting only: authority comes from the Second Brain
        runtime, not from parsing.
        """

        domain, separator, _ = skill_id.partition(".")

        if not separator or not domain:
            return None

        return domain

    def _emit(
        self,
        event: str,
        **data: Any,
    ) -> None:
        """
        Emit one lazy-skill lifecycle event.

        Skill bodies are never traced.
        """

        if self.trace is None:
            return

        self.trace.emit(
            component="worker_skill_runtime",
            event=event,
            request_id=self.request_id,
            task_id=self.task_id,
            attempt=self.attempt,
            loaded_skills=len(
                self.task_state.loaded_skills
            ),
            skill_limit=self.task_state.skill_limit,
            **data,
        )


class SkillToolClient:
    """
    Compose ``get_skill`` with the attempt's execution tool client.

    Execution tools are delegated unchanged. ``get_skill`` is runtime
    control capability and does not appear in Trusted Domain Grants.
    """

    def __init__(
        self,
        tools: ToolClient,
        skill_runtime: WorkerSkillRuntime,
    ) -> None:
        self.tools = tools
        self.skill_runtime = skill_runtime

    async def get_tools(
        self,
    ) -> list[dict[str, Any]]:
        return [
            *await self.tools.get_tools(),
            GET_SKILL_TOOL_SCHEMA,
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        if name == "get_skill":
            return json.dumps(
                self.skill_runtime.load_skill(
                    arguments.get("skill_id")
                ),
                default=str,
            )

        return await self.tools.call_tool(
            name=name,
            arguments=arguments,
        )
