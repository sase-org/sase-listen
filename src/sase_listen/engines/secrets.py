"""API key resolution from environment or an external command.

Owner: engines phase.

Secrets never appear in logs, manifests, errors, or `config` output: every
error message below names the *source* that failed, never the secret value.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Mapping

from sase_listen.engines.base import CredentialsError

#: Timeout for the external `api_key_command`, per the shared contracts.
COMMAND_TIMEOUT_S = 15


def resolve_api_key_with_source(
    *,
    engine: str,
    env_names: list[str],
    api_key_command: str = "",
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Return the (API key, source) for `engine`.

    The source is `env <NAME>` when an env var wins, else
    `engines.<engine>.api_key_command`. Never includes the secret value.
    """
    environ = env if env is not None else os.environ
    for name in env_names:
        value = environ.get(name, "").strip()
        if value:
            return value, f"env {name}"
    command = api_key_command.strip()
    if command:
        try:
            proc = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CredentialsError(
                f"No API key found for {engine}: api_key_command failed to run ({exc})."
            ) from exc
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.splitlines()[0].strip(), (
                f"engines.{engine}.api_key_command"
            )
        raise CredentialsError(
            f"No API key found for {engine}: api_key_command "
            f"produced no key (exit {proc.returncode})."
        )
    names = ", ".join(env_names) if env_names else "(no env names configured)"
    raise CredentialsError(
        f"No API key found for {engine}. "
        f"Set one of {names} or configure engines.{engine}.api_key_command."
    )


def resolve_api_key(
    *,
    engine: str,
    env_names: list[str],
    api_key_command: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the API key for `engine`.

    The first non-empty env var in `env_names` wins; then `api_key_command`
    runs without a shell and contributes its first output line. Raises
    CredentialsError (with no secret material) when nothing resolves.
    """
    key, _ = resolve_api_key_with_source(
        engine=engine,
        env_names=env_names,
        api_key_command=api_key_command,
        env=env,
    )
    return key


def describe_api_key_source(
    *,
    engine: str,
    env_names: list[str],
    api_key_command: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """Describe which source would supply the key, without running commands.

    Reports env-var presence only; the command itself is never executed.
    Used by `doctor` and credential-failure hints so the message names the
    source without revealing any value.
    """
    environ = env if env is not None else os.environ
    winner = ""
    for name in env_names:
        if environ.get(name, "").strip():
            winner = name
            break
    command = api_key_command.strip()
    if winner:
        if command:
            return (
                f"env {winner} (overrides engines.{engine}.api_key_command; "
                "presence only)"
            )
        return f"env {winner} (presence only)"
    if command:
        return f"engines.{engine}.api_key_command (presence only)"
    return "(no key configured)"


def hint_for_credentials_error(
    *,
    engine: str,
    env_names: list[str],
    api_key_command: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """Build a secret-free hint for a rejected/missing `engine` key."""
    environ = env if env is not None else os.environ
    winner = ""
    for name in env_names:
        if environ.get(name, "").strip():
            winner = name
            break
    command = api_key_command.strip()
    source = describe_api_key_source(
        engine=engine, env_names=env_names, api_key_command=api_key_command, env=env
    )
    if winner and command:
        return (
            f"Credentials failed (source: {source}). An env var won while "
            f"engines.{engine}.api_key_command is configured: unset {winner} "
            f"or pin engines.{engine}.api_key_env to the tool-specific variable."
        )
    if winner:
        return f"Credentials failed (source: {source}). Check that the key is valid."
    if command:
        return (
            f"Credentials failed (source: {source}). "
            f"Check engines.{engine}.api_key_command and the account quota."
        )
    names = ", ".join(env_names) if env_names else "(no env names configured)"
    return (
        f"Credentials failed (source: {source}). Set one of {names} or "
        f"configure engines.{engine}.api_key_command."
    )
    command = api_key_command.strip()
    if command:
        try:
            proc = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CredentialsError(
                f"No API key found for {engine}: api_key_command failed to run ({exc})."
            ) from exc
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.splitlines()[0].strip()
        raise CredentialsError(
            f"No API key found for {engine}: api_key_command "
            f"produced no key (exit {proc.returncode})."
        )
    names = ", ".join(env_names) if env_names else "(no env names configured)"
    raise CredentialsError(
        f"No API key found for {engine}. "
        f"Set one of {names} or configure engines.{engine}.api_key_command."
    )
