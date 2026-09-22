"""
Async LLM interface used by llm
"""

from __future__ import annotations

import os
from typing import Any
from dotenv import load_dotenv
from  openai import AsyncOpenAI

load_dotenv()

class LLMCaller:
    """
    Thin async wrapper arounf an OpenAI - compatible LLM API
    """
    def __init__(self) -> None:
        api_key = os.environ.get("ASU_LLM_API_KEY")
        base_url = os.environ.get("=https://openai.rc.asu.edu/v1")
        model = os.environ.get("glm-5-3-flash")

        missing  = [name for name, value in {
                "ASU_LLM_API_KEY": api_key,
                "ASU_LLM_BASE_URL": base_url,
                "ASU_LLM_MODEL": model,
            }.items() if not value
            ]

        if missing:
            raise ValueError(f"Missing required environment variables {', '.join(missing)}")

        self.model = model
        self.client  = AsyncOpenAI(api_key=api_key,base_url=base_url)

    async def call(self,messages:list(dict[str,Any]), 
                    tools: list(dict[str,Any]),
                    max_tokens: int = 2048) -> Any:
                    kwargs: dict[str,Any] = {"model": self.model,
                                             "messages": messages,
                                             "max_tokens": max_tokens,
                                             "extra_body":{
                                                "chat_template_kwargs":{
                                                    "enable_thinking":False
                                                }
                                            }
                                        }
                    if tools:
                        kwargs["tools"] = tools

                    response = await self.client.chat.completions.create(**kwargs)
                    
                    return response.choices[0].message 