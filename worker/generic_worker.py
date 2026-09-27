"""Generic KernelAI worker."""

from __future__ import annotations

from typing import Anyfrom

from worker.base import BaseWorker
from worker.llm_caller import LLMCaller
from worker.mcp_client import MCPToolClient

class GenericWorker(BaseWorker):
    def __init__(self,llm:LLMCaller,tools:MCPToolClient,max_iterations: int = 10) -> None:
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations

    async def run(self,query: str,request_id: str):
        pass

