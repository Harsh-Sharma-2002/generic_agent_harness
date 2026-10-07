from __future__ import annotations
from skills.registry import get_skill
from tasks.outcome import TaskOutcome
from tasks.task import Task, TaskStatus
from worker.clients.mcp import MCPToolClient
from worker.generic_worker import GenericWorker

class TaskExecutor:
    """
    Execute KernelAI tasks using GenericWorker instances.

    TaskExecutor translates a Task definition into the worker
    configuration required to execute it.
    """
    
    def __init__(self, 
    max_iterations: int = 10,
    verbose:bool = False,
    local: bool = False) -> None:
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.local = local


    async def execute(self,task:Task) -> TaskOutcome:
        """
        Execute one task and return its immutable outcome.
        """

        if task.status  != TaskStatus.QUEUED:
            raise ValueError(
                f"Task {task.task_id!r} cannot be executed "
                f"from status {task.status.value!r}."
            )

        if not task.skill_names:
            raise ValueError(
                f"Task {task.task_id!r} has no assigned skills."
            )
        
        skills = [get_skill(skill_name) for skill_name in task.skill_names]

