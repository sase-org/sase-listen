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
    environ = env if env is not None else os.environ
    for name in env_names:
        value = environ.get(name, "").strip()
        if value:
            return value
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
