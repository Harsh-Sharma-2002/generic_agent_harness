"""Guarded shell execution tool for the KernelAI MCP server."""

from __future__ import annotations

import asyncio
import os
import re
import signal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv()



# Configuration


WORKSPACE_ENV = "SHELL_WORKSPACE"

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 300.0

MAX_COMMAND_CHARS = 4_000
MAX_OUTPUT_CHARS = 100_000

# Prevent one Request with many shell-using Tasks from spawning an
# unlimited number of local processes.
MAX_CONCURRENT_SHELLS = 4

_shell_semaphore = asyncio.Semaphore(
    MAX_CONCURRENT_SHELLS
)


# Environment policy


# Do NOT forward the complete KernelAI environment into generated
# commands. In particular, this prevents the coding agent from
# trivially reading API/database credentials from its child process
# environment.

SAFE_ENV_KEYS = {
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TERM",
    "SHELL",
    "USER",
}

BLOCKED_ENV_PATTERNS = {
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "DATABASE_URL",
}



# Command policy


# These are defense-in-depth checks for obvious dangerous commands.
#
# They are NOT a security sandbox. The real filesystem boundary is
# expected to become a container/sandbox in a future version.

BLOCKED_PATTERNS = [
    # Privilege / account manipulation.
    r"\bsudo\b",
    r"\bsu\s",
    r"\bpasswd\b",

    # Obvious destructive filesystem operations.
    r"\brm\s+-[^\n]*r[^\n]*f\b",
    r"\brm\s+-[^\n]*f[^\n]*r\b",
    r"\bmkfs\b",
    r"\bdiskutil\s+erase",
    r"\bdd\s+.*\bof\s*=",

    # Machine lifecycle.
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",

    # Process-wide destructive behavior.
    r"\bkill\s+-9\s+-1\b",
    r"\bkillall\b",

    # macOS/system configuration.
    r"\blaunchctl\b",
    r"\bsystemsetup\b",
    r"\bcsrutil\b",
    r"\bnvram\b",

    # Shell escape patterns aimed at device files.
    r">\s*/dev/",
]



# Workspace



def _get_workspace() -> Path:
    """
    Return the configured shell workspace.

    Commands always start execution from this directory.
    """

    value = os.environ.get(
        WORKSPACE_ENV
    )

    if not value:
        raise ValueError(
            f"{WORKSPACE_ENV} environment variable is not set."
        )

    workspace = Path(
        value
    ).expanduser().resolve()

    if not workspace.exists():
        raise ValueError(
            f"Shell workspace does not exist: {workspace}"
        )

    if not workspace.is_dir():
        raise ValueError(
            f"Shell workspace is not a directory: {workspace}"
        )

    return workspace



# Environment



def _build_safe_environment() -> dict[str, str]:
    """
    Construct the environment inherited by shell subprocesses.

    KernelAI credentials are intentionally not propagated.
    """

    environment: dict[str, str] = {}

    for key in SAFE_ENV_KEYS:
        value = os.environ.get(
            key
        )

        if value is not None:
            environment[key] = value

    # Explicitly guard against accidentally adding sensitive variables
    # to SAFE_ENV_KEYS later.

    for key in list(
        environment
    ):
        upper_key = key.upper()

        if any(
            pattern in upper_key
            for pattern in BLOCKED_ENV_PATTERNS
        ):
            environment.pop(
                key,
                None,
            )

    return environment



# Validation

def _validate_command(
    command: str,
) -> str:
    """
    Validate basic command safety constraints.

    This provides defense in depth against obvious destructive commands.
    It must not be treated as a complete shell security boundary.
    """

    command = command.strip()

    if not command:
        raise ValueError(
            "command must not be empty."
        )

    if len(command) > MAX_COMMAND_CHARS:
        raise ValueError(
            "command exceeds the maximum allowed length "
            f"of {MAX_COMMAND_CHARS} characters."
        )

    for pattern in BLOCKED_PATTERNS:
        if re.search(
            pattern,
            command,
            flags=re.IGNORECASE,
        ):
            raise ValueError(
                "Command rejected by shell safety policy."
            )

    return command


def _validate_timeout(
    timeout_seconds: float,
) -> float:
    """
    Validate and normalize the requested command timeout.
    """

    if timeout_seconds <= 0:
        raise ValueError(
            "timeout_seconds must be greater than zero."
        )

    if timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise ValueError(
            "timeout_seconds cannot exceed "
            f"{MAX_TIMEOUT_SECONDS} seconds."
        )

    return float(
        timeout_seconds
    )



# Output



def _decode_output(
    value: bytes,
) -> tuple[str, bool]:
    """
    Decode and bound subprocess output.
    """

    text = value.decode(
        "utf-8",
        errors="replace",
    )

    truncated = (
        len(text)
        > MAX_OUTPUT_CHARS
    )

    if truncated:
        text = text[
            -MAX_OUTPUT_CHARS:
        ]

    return (
        text,
        truncated,
    )



# Process termination



async def _terminate_process(
    process: asyncio.subprocess.Process,
) -> None:
    """
    Terminate a command and its process group.

    This matters for commands such as pytest that may themselves spawn
    child processes.
    """

    if process.returncode is not None:
        return

    try:
        os.killpg(
            process.pid,
            signal.SIGTERM,
        )

    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=2.0,
        )

        return

    except asyncio.TimeoutError:
        pass

    try:
        os.killpg(
            process.pid,
            signal.SIGKILL,
        )

    except ProcessLookupError:
        return

    await process.wait()



# Shell tool



async def shell(
    command: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """
    Execute a guarded shell command in the configured workspace.

    Intended uses include:

    - inspecting repositories,
    - creating virtual environments,
    - installing project dependencies,
    - running tests,
    - running linters,
    - compiling code,
    - benchmarking programs,
    - inspecting local system/resource information.

    Commands execute asynchronously in a subprocess. A running command
    therefore does not block KernelAI's asyncio event loop.

    Safety properties:

    - fixed starting workspace,
    - sanitized subprocess environment,
    - KernelAI credentials are not inherited,
    - obvious destructive/system commands are rejected,
    - command length is bounded,
    - runtime is bounded,
    - output returned to the agent is bounded,
    - shell-process concurrency is bounded,
    - timed-out process groups are terminated.

    Important:

    This is NOT a complete operating-system sandbox. The subprocess still
    executes with the permissions of the user running KernelAI.

    Args:
        command:
            Shell command to execute.

        timeout_seconds:
            Maximum execution duration. Cannot exceed the configured
            maximum timeout.

    Returns:
        Structured command execution result.
    """

    command = _validate_command(
        command
    )

    timeout_seconds = _validate_timeout(
        timeout_seconds
    )

    workspace = _get_workspace()

    environment = _build_safe_environment()

    async with _shell_semaphore:

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(
                workspace
            ),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,

            # Give the command its own process group so timeout cleanup
            # can terminate descendants as well.
            start_new_session=True,
        )

        timed_out = False

        try:
            stdout_bytes, stderr_bytes = (
                await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds,
                )
            )

        except asyncio.TimeoutError:
            timed_out = True

            await _terminate_process(
                process
            )

            stdout_bytes, stderr_bytes = (
                await process.communicate()
            )

        except asyncio.CancelledError:
            # KernelAI cancellation must not leave orphaned test/build
            # processes running in the background.

            await _terminate_process(
                process
            )

            raise

    stdout, stdout_truncated = (
        _decode_output(
            stdout_bytes
        )
    )

    stderr, stderr_truncated = (
        _decode_output(
            stderr_bytes
        )
    )

    return {
        "command": command,
        "cwd": str(
            workspace
        ),
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }