"""
Measures average response time for ASU Research Computing's OpenAI-compatible
LLM gateway. Config lives in the gitignored .env at the repo root (see
.env.example): ASU_LLM_API_KEY, ASU_LLM_BASE_URL, ASU_LLM_MODEL.

Sends N sequential, non-streaming chat completions and reports wall-clock
latency stats. Runs with thinking off by default (chat.py's default); pass
--thinking to benchmark the model's reasoning path instead, or --both to
compare the two back to back.

Usage (from the repo root):
    .venv/bin/python scripts/benchmark_latency.py
    .venv/bin/python scripts/benchmark_latency.py -n 20
    .venv/bin/python scripts/benchmark_latency.py --model glm-5-3-flash
    .venv/bin/python scripts/benchmark_latency.py --both
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

DEFAULT_BASE_URL = "https://openai.rc.asu.edu/v1"
DEFAULT_MODEL = "glm-5-2"


def make_prompt() -> str:
    # The gateway appears to cache identical requests server-side -- a
    # repeated exact prompt came back in ~20ms, which is not a real
    # inference time. A per-call nonce forces a genuine cache miss so this
    # measures real response time, not a cache hit.
    return f"What is 2+2? Reply with just the number. (nonce {uuid.uuid4()})"


def get_client() -> OpenAI:
    api_key = os.environ.get("ASU_LLM_API_KEY")
    if not api_key:
        sys.exit(
            "ASU_LLM_API_KEY is not set.\n"
            "Set it in the gitignored .env at the repo root (see steps.md), "
            "or export it directly in your shell."
        )
    base_url = os.environ.get("ASU_LLM_BASE_URL", DEFAULT_BASE_URL)
    return OpenAI(api_key=api_key, base_url=base_url)


def run(client: OpenAI, model: str, n: int, thinking: bool, max_tokens: int) -> list[float]:
    extra_body = {} if thinking else {"chat_template_kwargs": {"enable_thinking": False}}
    label = "thinking on" if thinking else "thinking off"
    latencies: list[float] = []

    for i in range(1, n + 1):
        start = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": make_prompt()}],
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
        except Exception as exc:
            print(f"  [{label}] request {i}/{n} FAILED after {time.perf_counter() - start:.2f}s: "
                  f"{type(exc).__name__}: {exc}")
            continue
        elapsed = time.perf_counter() - start

        choice = response.choices[0]
        ok = bool(choice.message.content)
        note = "" if ok else f"  (empty content, finish_reason={choice.finish_reason!r})"
        print(f"  [{label}] request {i}/{n}: {elapsed:.3f}s{note}")
        latencies.append(elapsed)

    return latencies


def summarize(label: str, latencies: list[float]) -> None:
    if not latencies:
        print(f"\n{label}: no successful requests")
        return
    print(f"\n{label} ({len(latencies)} successful requests)")
    print(f"  mean:   {statistics.mean(latencies):.3f}s")
    print(f"  median: {statistics.median(latencies):.3f}s")
    print(f"  min:    {min(latencies):.3f}s")
    print(f"  max:    {max(latencies):.3f}s")
    if len(latencies) > 1:
        print(f"  stdev:  {statistics.stdev(latencies):.3f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    default_model = os.environ.get("ASU_LLM_MODEL", DEFAULT_MODEL)
    parser.add_argument("-n", "--num-requests", type=int, default=10, help="Requests per configuration (default: 10)")
    parser.add_argument("--model", default=default_model,
                         help=f"Model id (default: {default_model}, from ASU_LLM_MODEL)")
    parser.add_argument("--max-tokens", type=int, default=300, help="Max completion tokens per request (default: 300)")
    parser.add_argument("--thinking", action="store_true", help="Benchmark with thinking on instead of off")
    parser.add_argument("--both", action="store_true", help="Benchmark thinking off AND on, back to back")
    args = parser.parse_args()

    client = get_client()
    print(f"Model: {args.model}   requests per run: {args.num_requests}   "
          f"prompt: 'What is 2+2? ... (nonce)' -- a fresh nonce per call avoids the gateway's "
          f"apparent response cache\n")

    if args.both:
        off = run(client, args.model, args.num_requests, thinking=False, max_tokens=args.max_tokens)
        on = run(client, args.model, args.num_requests, thinking=True, max_tokens=args.max_tokens)
        summarize("Thinking OFF", off)
        summarize("Thinking ON", on)
    else:
        latencies = run(client, args.model, args.num_requests, thinking=args.thinking, max_tokens=args.max_tokens)
        summarize("Thinking ON" if args.thinking else "Thinking OFF", latencies)


if __name__ == "__main__":
    main()
