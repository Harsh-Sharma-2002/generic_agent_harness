"""KernalAI task representation"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import uuid
from winreg import QueryInfoKey

class TaskStatus(str,Enum):
    QUEUED = "queued"
    RUNNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class Task:
    query: str
    skill_name: str | None = None
    task_id: str = field(
        default_factory=lambda:str(uuid.uuid4())
    )
    parent_task_id: str | None  = None 

    status: TaskStatus = TaskStatus.QUEUED

    result: dict[str:Any] | None = None
    error: str | None = None
