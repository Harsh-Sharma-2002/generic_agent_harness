"""
Connectivity tests for ASU Research Computing's OpenAI-compatible LLM
gateway (see steps.md: "Worker, two-tier" / live path).

Config comes from the environment -- ASU_LLM_API_KEY, ASU_LLM_BASE_URL and
ASU_LLM_MODEL (see .env.example), loaded from the gitignored .env at the
repo root via conftest.py. The whole module is skipped, not failed, when
the key isn't present, so the suite still runs in environments (e.g. CI)
without the secret configured.
"""

from __future__ import annotations

import os

import pytest
from openai import AuthenticationError, OpenAI

API_KEY = os.environ.get("ASU_LLM_API_KEY")
BASE_URL = os.environ.get("ASU_LLM_BASE_URL", "https://openai.rc.asu.edu/v1")
MODEL = os.environ.get("ASU_LLM_MODEL", "glm-5-3-flash")

pytestmark = pytest.mark.skipif(
    not API_KEY, reason="ASU_LLM_API_KEY not set; skipping live gateway tests"
)


@pytest.fixture(scope="module")
def client() -> OpenAI:
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


def test_list_models_returns_known_model(client: OpenAI) -> None:
    model_ids = {model.id for model in client.models.list().data}
    assert model_ids, "gateway returned no models"
    assert MODEL in model_ids, f"{MODEL!r} not in available models: {sorted(model_ids)}"


def test_chat_completion_returns_nonempty_text(client: OpenAI) -> None:
    # GLM's hybrid-thinking models spend completion tokens on hidden
    # reasoning_content before emitting the actual answer when left on, so a
    # tight max_tokens budget can hit finish_reason="length" with content=None
    # even though the call itself succeeded. chat_template_kwargs.enable_thinking
    # turns that off (vLLM's hook for GLM's hybrid thinking mode), so a small
    # budget is enough again.
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Reply with exactly one word: pong"}],
        max_tokens=50,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    choice = response.choices[0]
    content = choice.message.content
    assert content and content.strip(), (
        f"gateway returned an empty completion (finish_reason={choice.finish_reason!r})"
    )


def test_invalid_key_is_rejected() -> None:
    bad_client = OpenAI(api_key="not-a-real-key", base_url=BASE_URL)
    with pytest.raises(AuthenticationError):
        bad_client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
