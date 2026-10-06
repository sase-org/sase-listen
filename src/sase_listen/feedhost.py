"""Publish to one feed host over SSH. Owner: feed-host phase.

Every machine renders locally. When ``feed.host`` names another machine,
publishing packs the library episode as an uncompressed tar and streams it
to ``sase-listen feed receive`` on the host. The host validates, imports
into its own library, and publishes under the feed lock. A local outbox
retries failed remote publishes.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import socket
import subprocess
import tarfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sase_listen.config import SaseListenConfig
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.feed import (
    RECEIVE_PROTOCOL,
    mark_manifest_published,
    mask_token_in_url,
    publish_episode,
    resolve_token,
)
from sase_listen.library import atomic_commit, episode_lock, episode_path
from sase_listen.paths import library_dir, state_dir

REMOTE_CALL_ENV = "SASE_LISTEN_REMOTE_CALL"
SSH_ENV = "SASE_LISTEN_SSH"
DEFAULT_SSH = "ssh"
RECEIVE_TIMEOUT_S = 600
DEFAULT_REMOTE_TIMEOUT_S = 60

EPISODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")
MEMBER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_RECEIVE_MEMBERS = 64
MAX_RECEIVE_BYTES = 256 * 1024 * 1024

PENDING_HINT = "sase-listen publish --pending"

PUBLICKEY_DENIED = "Permission denied (publickey"
SSH_ADD_TIMEOUT_S = 10


def describe_ssh_agent() -> str:
    """Describe the SSH agent in the current environment.

    Returns a sentence naming the agent state, or an empty string when the
    agent state cannot be determined. This helper never raises: a missing
    ``ssh-add`` binary, a timeout, or an ``OSError`` yields no description.
    """
    sock = os.environ.get("SSH_AUTH_SOCK", "").strip()
    if not sock:
        return "SSH_AUTH_SOCK is unset, so no SSH agent could offer a key"
    try:
        proc = subprocess.run(
            ["ssh-add", "-l"],
            capture_output=True,
            timeout=SSH_ADD_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode == 1:
        return f"the SSH agent at {sock} holds no identities"
    if proc.returncode == 2:
        return f"the SSH agent at {sock} is unreachable"
    if proc.returncode == 0:
        out = proc.stdout.decode("utf-8", errors="replace")
        count = len([line for line in out.splitlines() if line.strip()])
        return (
            f"the SSH agent at {sock} holds {count} "
            f"identit{'y' if count == 1 else 'ies'}, but the host accepted none"
        )
    return ""


class FeedHostUnreachable(SaseListenError):
    """Every SSH destination failed at the transport layer."""

    def __init__(self, destinations: Sequence[str], last_stderr: str) -> None:
        tried = ", ".join(destinations) if destinations else "(none)"
        line = last_stderr.strip().splitlines()[-1] if last_stderr.strip() else ""
        message = f"feed host unreachable (tried {tried})"
        if line:
            message = f"{message}: {line}"
        hint = "Check SSH BatchMode access and feed.host_ssh."
        if PUBLICKEY_DENIED in last_stderr:
            detail = describe_ssh_agent()
            if detail:
                message = f"{message} ({detail})"
            dest = destinations[-1] if destinations else "the feed host"
            hint = (
                "SSH reached the host but no key was accepted; load the key "
                "the host accepts into that agent (`ssh-add`), confirm "
                f"`ssh -o BatchMode=yes {dest} true` from the same environment, "
                f"then run `{PENDING_HINT}`."
            )
        super().__init__(
            message,
            ExitCode.UNEXPECTED,
            hint=hint,
        )
        self.destinations = list(destinations)
        self.last_stderr = last_stderr


def local_hostname() -> str:
    """Return the short, lowercased hostname of this machine."""
    return socket.gethostname().split(".", 1)[0].lower()


def is_remote_call() -> bool:
    """Return True when this process was invoked over the SSH transport."""
    return os.environ.get(REMOTE_CALL_ENV) == "1"


def feed_role(cfg: SaseListenConfig) -> str:
    """Return ``local`` or ``remote`` for this config on this machine."""
    host = cfg.feed.host.strip()
    if not host:
        return "local"
    short = host.split(".", 1)[0].lower()
    if short == local_hostname():
        return "local"
    return "remote"


def refuse_if_misrouted(cfg: SaseListenConfig) -> None:
    """Refuse a remote-call process that is not the configured feed host.

    Remote invocations set ``SASE_LISTEN_REMOTE_CALL=1`` and must never
    forward again. Exit 3 if this machine is not the feed host.
    """
    if is_remote_call() and feed_role(cfg) == "remote":
        host = cfg.feed.host.strip()
        raise SaseListenError(
            f"this machine ({local_hostname()}) is not the feed host ({host}); "
            "fix feed.host",
            ExitCode.CONFIG,
        )


def ssh_destinations(cfg: SaseListenConfig) -> list[str]:
    """Return SSH destinations to try, in order."""
    if cfg.feed.host_ssh:
        return [dest for dest in cfg.feed.host_ssh if dest]
    host = cfg.feed.host.strip()
    return [host] if host else []


def ssh_argv(dest: str, remote_args: Sequence[str]) -> list[str]:
    """Build the ssh argv that runs ``sase-listen`` on ``dest``."""
    ssh = os.environ.get(SSH_ENV, "").strip() or DEFAULT_SSH
    remote = shlex.join(["sase-listen", *remote_args])
    cmd = f'export PATH="$HOME/.local/bin:$PATH" {REMOTE_CALL_ENV}=1; exec {remote}'
    return [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        dest,
        cmd,
    ]


def _parse_json_object(text: str) -> dict[str, Any]:
    """Parse a single JSON object from remote stdout."""
    stripped = text.strip()
    if not stripped:
        raise json.JSONDecodeError("empty stdout", text, 0)
    try:
        payload: object = json.loads(stripped)
    except json.JSONDecodeError:
        line = stripped.splitlines()[-1]
        payload = json.loads(line)
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("expected a JSON object", stripped, 0)
    return payload


def _too_old_error(host: str) -> SaseListenError:
    return SaseListenError(
        f"sase-listen on {host} is too old to receive episodes; upgrade it there: "
        "`~/.local/bin/uv tool install --force "
        "git+https://github.com/sase-org/sase-listen`",
        ExitCode.UNEXPECTED,
    )


def _raise_remote_error(
    host: str, proc: subprocess.CompletedProcess[bytes], stdout: str, stderr: str
) -> None:
    """Raise a SaseListenError from a non-zero remote exit."""
    args_text = " ".join(str(a) for a in proc.args) if proc.args else ""
    if proc.returncode == 2 and "invalid choice" in stderr and "receive" in args_text:
        raise _too_old_error(host)
    try:
        payload = _parse_json_object(stdout)
    except (json.JSONDecodeError, ValueError):
        hint = stderr.strip().splitlines()[-1] if stderr.strip() else ""
        raise SaseListenError(
            f"on {host}: remote command failed (exit {proc.returncode})",
            ExitCode.UNEXPECTED,
            hint=hint,
        ) from None
    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "remote command failed")
        hint = str(error.get("hint") or "")
        raw_code = error.get("code", proc.returncode)
    else:
        message = str(payload.get("error") or "remote command failed")
        hint = ""
        raw_code = proc.returncode
    try:
        code = ExitCode(int(raw_code))
    except (TypeError, ValueError):
        code = ExitCode.UNEXPECTED
    raise SaseListenError(f"on {host}: {message}", code, hint=hint)


def run_remote(
    cfg: SaseListenConfig,
    args: Sequence[str],
    *,
    stdin: bytes | None = None,
    timeout_s: float = DEFAULT_REMOTE_TIMEOUT_S,
    on_attempt: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], str]:
    """Run ``sase-listen`` args on the feed host.

    Returns ``(json_object, destination_used)``. Tries each SSH destination
    and moves to the next only on a transport failure: ssh exit 255,
    ``OSError``, or a timeout before any output.
    """
    refuse_if_misrouted(cfg)
    host = cfg.feed.host.strip()
    dests = ssh_destinations(cfg)
    if not dests:
        raise FeedHostUnreachable([], "no SSH destinations configured")
    remote_args = list(args)
    if "--json" not in remote_args:
        remote_args.append("--json")
    last_stderr = ""
    prev_dest = ""
    for dest in dests:
        if on_attempt is not None:
            if prev_dest:
                on_attempt(f"{prev_dest} unreachable · trying {dest}")
            else:
                on_attempt(f"sending to {host} via {dest}")
        prev_dest = dest
        argv = ssh_argv(dest, remote_args)
        try:
            proc = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or b""
            err = (exc.stderr or b"").decode("utf-8", errors="replace")
            last_stderr = err or "timeout"
            if not out:
                continue
            raise SaseListenError(
                f"on {host}: remote command timed out",
                ExitCode.UNEXPECTED,
                hint=last_stderr.strip().splitlines()[-1]
                if last_stderr.strip()
                else "",
            ) from exc
        except OSError as exc:
            last_stderr = str(exc)
            continue
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if proc.returncode == 255:
            last_stderr = stderr or "ssh exit 255"
            continue
        if proc.returncode == 2 and "invalid choice" in stderr:
            raise _too_old_error(host)
        if proc.returncode != 0:
            _raise_remote_error(host, proc, stdout, stderr)
        try:
            payload = _parse_json_object(stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            raise SaseListenError(
                f"on {host}: remote command returned no JSON",
                ExitCode.UNEXPECTED,
                hint=stderr.strip().splitlines()[-1] if stderr.strip() else "",
            ) from exc
        return payload, dest
    raise FeedHostUnreachable(dests, last_stderr)


def pack_episode(episode_id: str, library: Path | None = None) -> bytes:
    """Build an uncompressed tar of the regular files in the episode dir."""
    lib = library if library is not None else library_dir()
    src = episode_path(episode_id, lib)
    manifest = src / "manifest.json"
    if not manifest.is_file():
        raise SaseListenError(
            f"Episode '{episode_id}' has no manifest in the library.",
            ExitCode.USAGE,
            hint="Render the episode first, then publish.",
        )
    names = sorted(
        path.name for path in src.iterdir() if path.is_file() and not path.is_symlink()
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in names:
            tar.add(src / name, arcname=name)
    return buf.getvalue()


def _validate_member(member: tarfile.TarInfo) -> None:
    name = member.name
    if name.startswith("/") or "/" in name or name.startswith("..") or ".." in name:
        raise SaseListenError(
            f"episode archive member '{name}' is not a plain file name.",
            ExitCode.USAGE,
            hint="Re-pack the episode with sase-listen publish.",
        )
    if not MEMBER_NAME_RE.match(name):
        raise SaseListenError(
            f"episode archive member '{name}' is not a permitted file name.",
            ExitCode.USAGE,
        )
    if member.issym() or member.islnk():
        raise SaseListenError(
            f"episode archive member '{name}' is a symlink.",
            ExitCode.USAGE,
        )
    if member.isdir():
        raise SaseListenError(
            f"episode archive member '{name}' is a directory.",
            ExitCode.USAGE,
        )
    if not member.isreg():
        raise SaseListenError(
            f"episode archive member '{name}' is not a regular file.",
            ExitCode.USAGE,
        )


def receive_episode(
    episode_id: str,
    data: bytes,
    cfg: SaseListenConfig,
    *,
    library: Path | None = None,
) -> dict[str, Any]:
    """Validate a packed episode, import it, and publish on this host."""
    refuse_if_misrouted(cfg)
    if feed_role(cfg) != "local":
        host = cfg.feed.host.strip()
        raise SaseListenError(
            f"this machine ({local_hostname()}) is not the feed host ({host}); "
            "fix feed.host",
            ExitCode.CONFIG,
        )
    if not EPISODE_ID_RE.match(episode_id):
        raise SaseListenError(
            f"invalid episode id '{episode_id}'.",
            ExitCode.USAGE,
        )
    if len(data) > MAX_RECEIVE_BYTES:
        raise SaseListenError(
            "episode archive exceeds 256 MiB.",
            ExitCode.USAGE,
        )
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            members = tar.getmembers()
            if len(members) > MAX_RECEIVE_MEMBERS:
                raise SaseListenError(
                    "episode archive has too many members.",
                    ExitCode.USAGE,
                )
            total = 0
            payloads: dict[str, bytes] = {}
            for member in members:
                _validate_member(member)
                total += max(0, int(member.size))
                if total > MAX_RECEIVE_BYTES:
                    raise SaseListenError(
                        "episode archive exceeds 256 MiB.",
                        ExitCode.USAGE,
                    )
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise SaseListenError(
                        f"episode archive member '{member.name}' could not be read.",
                        ExitCode.USAGE,
                    )
                payloads[member.name] = extracted.read()
    except tarfile.TarError as exc:
        raise SaseListenError(
            "episode archive is not a valid tar.",
            ExitCode.USAGE,
            hint="Re-pack the episode with sase-listen publish.",
        ) from exc
    if "manifest.json" not in payloads:
        raise SaseListenError(
            "episode archive is missing manifest.json.",
            ExitCode.USAGE,
        )
    try:
        manifest = json.loads(payloads["manifest.json"].decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SaseListenError(
            "episode archive manifest.json is not valid JSON.",
            ExitCode.USAGE,
        ) from exc
    if not isinstance(manifest, dict):
        raise SaseListenError(
            "episode archive manifest.json must be a JSON object.",
            ExitCode.USAGE,
        )
    if str(manifest.get("episode_id", "")) != episode_id:
        raise SaseListenError(
            "episode archive manifest episode_id does not match the argument.",
            ExitCode.USAGE,
        )
    audio = manifest.get("audio")
    audio_file = (
        audio.get("file")
        if isinstance(audio, dict) and isinstance(audio.get("file"), str)
        else ""
    )
    if not audio_file or audio_file not in payloads:
        raise SaseListenError(
            "episode archive is missing the manifest audio file.",
            ExitCode.USAGE,
        )
    lib = library if library is not None else library_dir()
    with episode_lock(episode_id):
        atomic_commit(episode_id, payloads, lib)
    result = publish_episode(episode_id, cfg, library=lib)
    mark_manifest_published(episode_id, lib)
    token = resolve_token(cfg)
    result["item_url"] = mask_token_in_url(str(result.get("item_url", "")), token)
    result["ok"] = True
    result["receive_protocol"] = RECEIVE_PROTOCOL
    return result


def outbox_dir() -> Path:
    """Return the retry-outbox directory."""
    return state_dir() / "outbox"


def _outbox_path(episode_id: str) -> Path:
    return outbox_dir() / f"{episode_id}.json"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{os.getpid()}-{path.name}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def queue_publish(episode_id: str, last_error: str = "") -> dict[str, Any]:
    """Queue (or update) a pending remote publish for ``episode_id``."""
    path = _outbox_path(episode_id)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = {}
    attempts = int(existing.get("attempts", 0) or 0) + 1
    queued_at = str(existing.get("queued_at") or "") or datetime.now(UTC).isoformat(
        timespec="seconds"
    )
    entry = {
        "episode_id": episode_id,
        "queued_at": queued_at,
        "attempts": attempts,
        "last_error": last_error,
    }
    _atomic_write(path, (json.dumps(entry, indent=2, sort_keys=True) + "\n").encode())
    return entry


def pending_publishes() -> list[dict[str, Any]]:
    """Return outbox entries, sorted by episode id."""
    root = outbox_dir()
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(loaded, dict) and loaded.get("episode_id"):
            found.append(loaded)
    return found


def _drop_outbox(episode_id: str) -> None:
    path = _outbox_path(episode_id)
    if path.is_file():
        path.unlink()


def _push_episode(
    episode_id: str,
    cfg: SaseListenConfig,
    *,
    library: Path | None = None,
    show_url: bool = False,
) -> dict[str, Any]:
    """Publish one episode without flushing the rest of the outbox."""
    if feed_role(cfg) == "remote":
        blob = pack_episode(episode_id, library)
        payload, dest = run_remote(
            cfg,
            ["feed", "receive", episode_id, "--json"],
            stdin=blob,
            timeout_s=RECEIVE_TIMEOUT_S,
        )
        payload["host"] = cfg.feed.host.strip()
        payload["via"] = dest
        return payload
    result = publish_episode(episode_id, cfg, library=library)
    if not show_url:
        result["item_url"] = mask_token_in_url(
            str(result.get("item_url", "")), resolve_token(cfg)
        )
    result["host"] = local_hostname()
    result["via"] = "local"
    return result


def flush_pending(
    cfg: SaseListenConfig, *, on_step: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """Retry every queued publish. Returns published and failed lists."""
    published: list[str] = []
    failed: list[dict[str, str]] = []
    queued = [
        str(entry.get("episode_id", ""))
        for entry in pending_publishes()
        if str(entry.get("episode_id", ""))
    ]
    if on_step is not None and queued:
        on_step(f"sending {len(queued)} queued episode(s) first")
    for position, episode_id in enumerate(queued, start=1):
        if on_step is not None:
            on_step(f"sending queued episode {position} of {len(queued)}")
        try:
            _push_episode(episode_id, cfg)
        except SaseListenError as exc:
            queue_publish(episode_id, str(exc))
            failed.append({"episode_id": episode_id, "error": str(exc)})
        else:
            _drop_outbox(episode_id)
            published.append(episode_id)
    return {"published": published, "failed": failed}


def publish_any(
    episode_id: str,
    cfg: SaseListenConfig,
    *,
    library: Path | None = None,
    show_url: bool = False,
    on_step: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Publish locally or stream the episode to the feed host.

    Remote role: best-effort flush of the outbox, then pack and receive.
    """
    refuse_if_misrouted(cfg)
    if feed_role(cfg) == "remote":
        with contextlib.suppress(SaseListenError):
            flush_pending(cfg, on_step=on_step)
        if on_step is not None:
            on_step("packing the episode")
        host = cfg.feed.host.strip()

        def _attempt(text: str) -> None:
            if on_step is not None:
                on_step(text)

        payload, dest = run_remote(
            cfg,
            ["feed", "receive", episode_id, "--json"],
            stdin=pack_episode(episode_id, library),
            timeout_s=RECEIVE_TIMEOUT_S,
            on_attempt=_attempt,
        )
        payload["host"] = host
        payload["via"] = dest
        return payload
    if on_step is not None:
        on_step("updating the feed")
    return _push_episode(episode_id, cfg, library=library, show_url=show_url)
