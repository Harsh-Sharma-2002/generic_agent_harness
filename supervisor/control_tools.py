"""Privileged control tools available to a KernelAI Supervisor."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from skills.registry import get_skill, list_skills
from tasks.task import Task


if TYPE_CHECKING:
    from supervisor.supervisor import Supervisor


LIST_SKILLS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": (
            "List the worker skills currently available "
            "for task delegation."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}


DELEGATE_TASK_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "delegate_task",
        "description": (
            "Create one independently executable KernelAI task "
            "using one or more available skills."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Clear, self-contained instruction describing "
                        "the work the Task Worker must complete."
                    ),
                },
                "skills": {
                    "type": "array",
                    "description": (
                        "Names of the worker skills required to "
                        "complete this task."
                    ),
                    "items": {
                        "type": "string",
                    },
                    "minItems": 1,
                },
            },
            "required": [
                "task",
                "skills",
            ],
            "additionalProperties": False,
        },
    },
}


class SupervisorControlTools:
    """
    Privileged model-facing operations available to one Supervisor.

    These tools may create Tasks for the Supervisor's Request,
    but they never execute those Tasks.

    Task execution remains the responsibility of the Supervisor
    runtime.
    """

    def __init__(
        self,
        supervisor: Supervisor,
    ) -> None:
        self.supervisor = supervisor

    async def list_skills(
        self,
        _: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Return lightweight descriptions of currently available skills.
        """

        return {
            "skills": list_skills(),
        }

    async def delegate_task(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, str]:
        """
        Validate and register one Task for later execution.

        The Task is added to the Supervisor's current pending batch.
        Execution does not happen inside this control tool.
        """

        task_query = arguments.get(
            "task"
        )

        skill_names = arguments.get(
            "skills"
        )

        # ---------------------------------------------------------
        # Validate task instruction
        # ---------------------------------------------------------

        if not isinstance(
            task_query,
            str,
        ):
            raise ValueError(
                "delegate_task requires 'task' to be a string."
            )

        task_query = task_query.strip()

        if not task_query:
            raise ValueError(
                "delegate_task requires a non-empty 'task'."
            )

        # ---------------------------------------------------------
        # Validate skill list
        # ---------------------------------------------------------

        if not isinstance(
            skill_names,
            list,
        ) or not skill_names:
            raise ValueError(
                "delegate_task requires a non-empty 'skills' list."
            )

        normalized_skills: list[str] = []

        for skill_name in skill_names:
            if not isinstance(
                skill_name,
                str,
            ):
                raise ValueError(
                    "Every skill name must be a string."
                )

            skill_name = skill_name.strip()

            if not skill_name:
                raise ValueError(
                    "Skill names cannot be empty."
                )

            # Deterministically verify that the requested
            # skill currently exists.
            get_skill(
                skill_name
            )

            if skill_name not in normalized_skills:
                normalized_skills.append(
                    skill_name
                )

        # ---------------------------------------------------------
        # Create Task
        # ---------------------------------------------------------

        task_id = (
            f"task_{uuid.uuid4().hex}"
        )

        task = Task(
            request_id=self.supervisor.request.request_id,
            task_id=task_id,
            query=task_query,
            skill_names=normalized_skills,
        )

        # Lifetime registry for this Request.
        self.supervisor.tasks[
            task_id
        ] = task

        # Current execution batch.
        self.supervisor.pending_tasks.append(
            task
        )

        # The model only needs the identifier.
        return {
            "task_id": task_id,
        }

    def get_tools(
        self,
    ) -> dict[
        str,
        tuple[
            dict[str, Any],
            Any,
        ],
    ]:
        """
        Return control tools in ControlToolClient registration format.
        """

        return {
            "list_skills": (
                LIST_SKILLS_SCHEMA,
                self.list_skills,
            ),
            "delegate_task": (
                DELEGATE_TASK_SCHEMA,
                self.delegate_task,
            ),
        }