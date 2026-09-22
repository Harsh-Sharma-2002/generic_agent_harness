from abc import ABC, abstractmethod
from typing import Any

class BaseWorker(ABC):
    """
    Base contract for all kernelAI workers.
    """

    @abstractmethod
    async def run(self,query: str, request_id: str) -> dict[str,Any]:
        ...