"""Privileged control tools available only to the KernelAI orchestrator."""

from __future__ import annotations
from ast import arg
from typing import Any
from orchestrator.orchestrator import Orchestrator

from skills.registry import get_skill,list_skills

LIST_SKILLS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": (
            "List the worker skills currently available for delegation."
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
            "Create one independently schedulable worker task using "
            "one or more available skills."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Clear, self-contained instruction describing "
                        "the work the worker must perform."
                    ),
                },
                "skills": {
                    "type": "array",
                    "description": (
                        "Names of the worker skills required to complete "
                        "this task."
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

class ControlTools:
    """
    Privileged model-facing interface to the KernelAI runtime.

    A ControlTools instance is bound to exactly one user request.

    Ordinary GenericWorkers never receive these tools.
    """
    def __init__(self,orchestrator:Orchestrator, request_id: str) -> None:
        self.orchestrator = orchestrator
        self.request_id = request_id

    
    async def list_skills(self,_: dict[str,Any]) -> dict[str,Any]:
        """
        Return lightweight descriptions of currently available skills.
        """
        return {
            "skills": list_skills()
        }

    async def delegate_task(self,arguments: dict[str,Any]) -> dict[str,Any]:
        """
        Validate a delegation request and create the corresponding task.
        """

        task_query = arguments.get("task")
        skill_names = arguments.get("skills")

        if not isinstance(task_query,str):
            raise ValueError("delegate_task requires 'task' to be a string.")

        task_query = task_query.strip()

        if not task_query:
            raise ValueError("delegate_task requires a non-empty 'task'.")

        if not isinstance(skill_names,list) or not skill_names:
            raise ValueError("delegate_task requires a non-empty 'skills' list.")

        normalized_skills: list[str] = []

        for skill_name in skill_names:
            if not isinstance(skill_name,str):
                raise ValueError("Every skill name must be a string.")

            skill_name = skill_name.strip()

            if not skill_name:
                raise ValueError("Skill names cannot be empty.")

            # Deterministic capability validation

            get_skill(skill_name)

            if skill_name not in normalized_skills:
                normalized_skills.append(skill_name)

        task = await self.orchestrator.create_task(
            request_id=self.request_id,
            task_query=task_query,
            skill_names=normalized_skills,
        )

        return {
            "request_id": task.request_id,
            "task_id": task.task_id,
            "status": task.status.value,
        }

    def get_local_tools(self) -> dict[str, tuple[dict[str, Any], Any]]:
        """
        Return privileged tools in CompositeToolClient format.
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





