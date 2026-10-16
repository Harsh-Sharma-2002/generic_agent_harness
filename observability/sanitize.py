"""Sanitization and bounding helpers for KernelAI observability.

These helpers shape tool-call payloads for observability events.

They are strictly observability-only: they describe execution,
they never influence it.
"""

from __future__ import annotations

import json
from typing import Any

# Trace bounds (in bytes, UTF-8 encoded).

MAX_ARGUMENTS_BYTES = 4096
MAX_RESULT_PREVIEW_BYTES = 8192

# Console bound (in characters, per rendered value).

CONSOLE_VALUE_CHAR_LIMIT = 120

# Marker used to replace secret values.

REDACTED = "[REDACTED]"

# Substring markers that identify a secret-bearing key.

_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "credential",
    "private_key",
    "access_key",
    "api_key",
    "apikey",
    "authorization",
)


def _is_secret_key(key: str) -> bool:
    """
    Return True when a mapping key looks like it carries a secret.
    """

    lowered = key.lower()

    if any(
        marker in lowered
        for marker in _SECRET_KEY_MARKERS
    ):
        return True

    return lowered == "token" or lowered.endswith(
        "_token"
    )


def _redact(
    value: Any,
    secrets: list[str],
) -> Any:
    """
    Recursively build a redacted copy of ``value``.

    Original secret values are collected into ``secrets`` so error
    messages can be scrubbed of the same secrets later.
    """

    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}

        for key, item in value.items():

            if (
                isinstance(key, str)
                and _is_secret_key(key)
            ):
                if (
                    isinstance(item, str)
                    and item
                    and item != REDACTED
                ):
                    secrets.append(item)

                redacted[key] = REDACTED

            else:
                redacted[key] = _redact(
                    item,
                    secrets,
                )

        return redacted

    if isinstance(value, list):
        return [
            _redact(item, secrets)
            for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            _redact(item, secrets)
            for item in value
        )

    return value


def redact_secrets(
    value: Any,
) -> tuple[Any, list[str]]:
    """
    Return a non-mutating redacted copy of ``value`` together with
    the list of original secret values it contained.
    """

    secrets: list[str] = []

    return _redact(value, secrets), secrets


def bound_text(
    text: str,
    limit_bytes: int,
) -> str:
    """
    Bound text to at most ``limit_bytes`` UTF-8 bytes without
    splitting a multi-byte character.
    """

    encoded = text.encode("utf-8")

    if len(encoded) <= limit_bytes:
        return text

    return encoded[:limit_bytes].decode(
        "utf-8",
        errors="ignore",
    )


def build_arguments_trace(
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Build the canonical ``arguments`` trace fields for a
    tool_call_started event.

    Within ``MAX_ARGUMENTS_BYTES`` the ``arguments`` field holds the
    structured sanitized dict. Above the limit it holds the bounded
    serialized string and ``arguments_truncated`` is True.

    Returns the trace fields and the collected secret values.
    """

    sanitized, secret_values = redact_secrets(
        arguments
    )

    serialized = json.dumps(
        sanitized,
        default=str,
        ensure_ascii=False,
    )

    size_bytes = len(
        serialized.encode("utf-8")
    )

    if size_bytes <= MAX_ARGUMENTS_BYTES:
        return (
            {
                "arguments": sanitized,
                "arguments_truncated": False,
                "arguments_size_bytes": size_bytes,
            },
            secret_values,
        )

    return (
        {
            "arguments": bound_text(
                serialized,
                MAX_ARGUMENTS_BYTES,
            ),
            "arguments_truncated": True,
            "arguments_size_bytes": size_bytes,
        },
        secret_values,
    )


def build_result_trace(
    result: Any,
) -> dict[str, Any]:
    """
    Build the ``result_preview`` trace fields for a
    tool_call_completed event.

    ``result_preview`` is always a string, bounded to
    ``MAX_RESULT_PREVIEW_BYTES``.
    """

    preview_source = (
        result
        if isinstance(result, str)
        else str(result)
    )

    size_bytes = len(
        preview_source.encode("utf-8")
    )

    return {
        "result_preview": bound_text(
            preview_source,
            MAX_RESULT_PREVIEW_BYTES,
        ),
        "result_truncated": (
            size_bytes > MAX_RESULT_PREVIEW_BYTES
        ),
        "result_size_bytes": size_bytes,
        "result_type": type(result).__name__,
    }


def scrub_error_message(
    message: str,
    secret_values: list[str],
) -> str:
    """
    Scrub known secret values out of an error message.
    """

    scrubbed = message

    for secret in secret_values:
        if secret in scrubbed:
            scrubbed = scrubbed.replace(
                secret,
                REDACTED,
            )

    return scrubbed


def cap_value(
    value: str,
    limit: int = CONSOLE_VALUE_CHAR_LIMIT,
) -> str:
    """
    Cap one rendered console value for terminal display.
    """

    if len(value) <= limit:
        return value

    return value[:limit] + "..."
