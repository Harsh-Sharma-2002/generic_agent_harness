"""
Interactive, continuous chat against ASU Research Computing's OpenAI-compatible
LLM gateway. Config lives in the gitignored .env at the repo root (see
.env.example): ASU_LLM_API_KEY, ASU_LLM_BASE_URL, ASU_LLM_MODEL.

Thinking is off by default: the default model is a hybrid reasoning model,
and its hidden reasoning_content burns completion tokens before the real
answer shows up (see tests/test_asu_llm_api.py). --thinking turns it back on.

Usage (from the repo root):
    .venv/bin/python scripts/chat.py
    .venv/bin/python scripts/chat.py --model glm-5-3-flash
    .venv/bin/python scripts/chat.py --thinking

Type 'exit', 'quit', or press Ctrl+D to end the session.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

DEFAULT_BASE_URL = "https://openai.rc.asu.edu/v1"
DEFAULT_MODEL = "glm-5-2"


def get_client() -> OpenAI:
    api_key = os.environ.get("ASU_LLM_API_KEY")
    if not api_key:
        sys.exit(
            "ASU_LLM_API_KEY is not set.\n"
            "Set it in the gitignored .env at the repo root (see .env.example), "
            "or export it directly in your shell."
        )
    base_url = os.environ.get("ASU_LLM_BASE_URL", DEFAULT_BASE_URL)
    return OpenAI(api_key=api_key, base_url=base_url)


def main() -> None:
    default_model = os.environ.get("ASU_LLM_MODEL", DEFAULT_MODEL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=default_model,
                         help=f"Model id (default: {default_model}, from ASU_LLM_MODEL)")
    parser.add_argument("--max-tokens", type=int, default=800, help="Max completion tokens per turn (default: 800)")
    parser.add_argument(
        "--thinking", action="store_true",
        help="Leave the model's reasoning trace on (off by default)",
    )
    args = parser.parse_args()

    client = get_client()
    messages: list[dict] = []
    extra_body = {} if args.thinking else {"chat_template_kwargs": {"enable_thinking": False}}

    print(
        f"Chatting with {args.model} (thinking {'on' if args.thinking else 'off'}). "
        "Type 'exit' or 'quit', or press Ctrl+D, to end.\n"
    )

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        messages.append({"role": "user", "content": user_input})

        try:
            response = client.chat.completions.create(
                model=args.model,
                messages=messages,
                max_tokens=args.max_tokens,
                extra_body=extra_body,
            )
        except Exception as exc:
            print(f"[error] {type(exc).__name__}: {exc}")
            messages.pop()  # drop the turn that failed so a retry doesn't duplicate it
            continue

        choice = response.choices[0]
        reply = choice.message.content

        if not reply:
            print(f"[warning] empty reply (finish_reason={choice.finish_reason!r}); try --max-tokens higher.")
            messages.pop()
            continue

        print(f"assistant> {reply}\n")
        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
