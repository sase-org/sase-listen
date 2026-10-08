"""Precise build identity for multi-machine installs. Owner: scaffold phase.

Every install reports exactly which build it runs so renderer/feed-host
drift fails loudly with the repair command instead of a traceback.

Stdlib-only plus ``sase_listen.errors`` so this module still imports when
third-party dependencies are missing. It must never raise.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Any

_DIST_NAME = "sase-listen"
_GIT_TIMEOUT_S = 2.0

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)")
_NORMALIZE_RE = re.compile(r"[-_.]+")
_VERSION_PART_RE = re.compile(r"^(\d+)")


@dataclass(frozen=True)
class BuildInfo:
    """One install's precise build identity."""

    install: str = "unknown"
    source: str = ""
    version: str = "unknown"
    metadata_version: str = "unknown"
    commit: str = ""
    dirty: bool = False
    missing_dependencies: tuple[str, ...] = field(default_factory=tuple)

    @property
    def stale(self) -> bool:
        """True when the env is out of date with its code."""
        if self.missing_dependencies:
            return True
        return self.install == "editable" and self.metadata_version != self.version

    def display(self) -> str:
        """Human build string, e.g. ``0.1.1 (editable @ 3ae7310)``."""
        if self.install == "index":
            return self.version
        if self.install == "vcs":
            if self.commit:
                return f"{self.version} (git {self.commit[:7]})"
            return f"{self.version} (git)"
        if self.install == "editable":
            if self.commit:
                base = f"{self.version} (editable @ {self.commit[:7]}"
            else:
                base = f"{self.version} (editable"
            if self.dirty:
                base += ", dirty"
            return base + ")"
        if self.install == "local":
            return f"{self.version} (local)"
        return self.version

    def upgrade_command(self) -> str:
        """One copy-pasteable repair command for this install."""
        receipt = Path(sys.prefix) / "uv-receipt.toml"
        if receipt.is_file():
            if _receipt_first_requirement(receipt) == "sase":
                # This interpreter is the sase tool environment with listen
                # installed as a plugin: repair it through the plugin manager.
                return "sase plugin update listen"
            uv = shutil.which("uv") or "uv"
            if self.install == "editable" and self.source:
                quoted = shlex.quote(self.source)
                return (
                    f"git -C {quoted} pull --ff-only"
                    f" && {uv} tool upgrade --reinstall sase-listen"
                )
            if self.install == "index":
                return f"{uv} tool upgrade sase-listen"
            if self.install == "vcs" and self.source:
                return f"{uv} tool install --force {self.source}"
            return f"{uv} tool upgrade --reinstall sase-listen"
        return (
            "reinstall with the installer you used,"
            " for example `pipx reinstall sase-listen`"
        )

    def to_json(self) -> dict[str, Any]:
        """JSON-safe dict of every build field plus display and repair."""
        return {
            "install": self.install,
            "source": self.source,
            "version": self.version,
            "metadata_version": self.metadata_version,
            "commit": self.commit,
            "dirty": self.dirty,
            "missing_dependencies": list(self.missing_dependencies),
            "stale": self.stale,
            "display": self.display(),
            "upgrade_command": self.upgrade_command(),
        }


@dataclass(frozen=True)
class BuildComparison:
    """Outcome of comparing a local and a remote build."""

    outcome: str
    message: str


def _file_url_to_path(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "file":
            return ""
        path = urllib.parse.unquote(parsed.path)
        return path or ""
    except Exception:
        return ""


def _read_dist() -> tuple[str, dict[str, Any] | None]:
    try:
        dist = metadata.distribution(_DIST_NAME)
    except metadata.PackageNotFoundError:
        return "", None
    except Exception:
        return "", None
    try:
        meta_version: str = dist.version
    except Exception:
        meta_version = "unknown"
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        raw = None
    if not raw:
        return meta_version, None
    try:
        payload = json.loads(raw)
    except Exception:
        return meta_version, None
    if not isinstance(payload, dict):
        return meta_version, None
    return meta_version, payload


def _project_version(source_path: str) -> str:
    try:
        text = (Path(source_path) / "pyproject.toml").read_bytes()
    except Exception:
        return ""
    try:
        data = tomllib.loads(text.decode("utf-8"))
    except Exception:
        return ""
    try:
        project = data.get("project")
        if isinstance(project, dict):
            version = project.get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
    except Exception:
        return ""
    return ""


def _git_output(source_path: str, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", source_path, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _normalize_name(name: str) -> str:
    return _NORMALIZE_RE.sub("-", name).lower()


def _receipt_first_requirement(receipt: Path) -> str:
    """Return the normalized first requirement name in a uv receipt."""
    try:
        text = receipt.read_bytes()
    except Exception:
        return ""
    try:
        data = tomllib.loads(text.decode("utf-8"))
    except Exception:
        return ""
    try:
        tool = data.get("tool")
        requirements = tool.get("requirements") if isinstance(tool, dict) else None
    except Exception:
        return ""
    if not isinstance(requirements, list) or not requirements:
        return ""
    first = requirements[0]
    if isinstance(first, dict):
        name = first.get("name")
    elif isinstance(first, str):
        name = first
    else:
        return ""
    if not isinstance(name, str) or not name.strip():
        return ""
    return _normalize_name(name.strip())


def _missing_deps(source_path: str) -> tuple[str, ...]:
    try:
        text = (Path(source_path) / "pyproject.toml").read_bytes()
    except Exception:
        return ()
    try:
        data = tomllib.loads(text.decode("utf-8"))
    except Exception:
        return ()
    try:
        project = data.get("project")
        deps = project.get("dependencies") if isinstance(project, dict) else None
    except Exception:
        return ()
    if not isinstance(deps, list):
        return ()
    missing: list[str] = []
    for item in deps:
        if not isinstance(item, str) or not item.strip():
            continue
        # Skip requirements with environment markers: evaluating them
        # needs `packaging`, which this stdlib-only module avoids.
        if ";" in item:
            continue
        match = _NAME_RE.match(item)
        if not match:
            continue
        name = _normalize_name(match.group(1))
        if not name:
            continue
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
        except Exception:
            # Any lookup failure must not become a false positive.
            continue
    return tuple(missing)


def _compute() -> BuildInfo:
    try:
        return _compute_inner()
    except Exception:
        return BuildInfo()


def _compute_inner() -> BuildInfo:
    meta_version, payload = _read_dist()
    if not meta_version:
        meta_version = "unknown"
    if payload is None:
        if meta_version == "unknown":
            return BuildInfo()
        return BuildInfo(
            install="index",
            version=meta_version,
            metadata_version=meta_version,
        )
    url = payload.get("url") if isinstance(payload.get("url"), str) else ""
    url = url or ""
    dir_info = payload.get("dir_info")
    vcs_info = payload.get("vcs_info")
    archive_info = payload.get("archive_info")
    if isinstance(dir_info, dict) and bool(dir_info.get("editable")):
        source = _file_url_to_path(url)
        version = _project_version(source) if source else ""
        if not version:
            version = meta_version
        commit = _git_output(source, ["rev-parse", "HEAD"]) if source else ""
        dirty = False
        if source:
            porcelain = _git_output(
                source, ["status", "--porcelain", "--untracked-files=no"]
            )
            # _git_output returns "" on failure; an empty repo status is
            # also "". Only treat non-empty output as dirty, and only when
            # the rev-parse above succeeded (else git is unusable here).
            dirty = bool(commit) and bool(porcelain)
            if not commit:
                dirty = False
        missing = _missing_deps(source) if source else ()
        return BuildInfo(
            install="editable",
            source=source,
            version=version,
            metadata_version=meta_version,
            commit=commit,
            dirty=dirty,
            missing_dependencies=missing,
        )
    if isinstance(vcs_info, dict):
        raw_commit = vcs_info.get("commit_id")
        commit_id = raw_commit if isinstance(raw_commit, str) else ""
        return BuildInfo(
            install="vcs",
            source=url,
            version=meta_version,
            metadata_version=meta_version,
            commit=commit_id,
        )
    if isinstance(dir_info, dict) or isinstance(archive_info, dict):
        return BuildInfo(
            install="local",
            version=meta_version,
            metadata_version=meta_version,
        )
    return BuildInfo(
        install="index",
        version=meta_version,
        metadata_version=meta_version,
    )


@cache
def current() -> BuildInfo:
    """Return this install's build identity (computed once per process)."""
    return _compute()


def _parse_version_tuple(version: str) -> tuple[int, ...] | None:
    parts = str(version).strip().split(".")
    if not parts or not parts[0]:
        return None
    numbers: list[int] = []
    for part in parts:
        match = _VERSION_PART_RE.match(part.strip())
        if not match:
            return None
        try:
            numbers.append(int(match.group(1)))
        except ValueError:
            return None
    return tuple(numbers)


def compare_builds(
    local: dict[str, Any] | None, remote: dict[str, Any] | None
) -> BuildComparison:
    """Compare local and remote ``to_json()`` dicts."""
    if not isinstance(remote, dict) or not remote.get("version"):
        return BuildComparison(
            outcome="remote_unknown",
            message=(
                "feed host reports no build info (it predates build tracking);"
                " upgrade sase-listen there (see docs/multi-machine.md)"
            ),
        )
    if not isinstance(local, dict) or not local.get("version"):
        return BuildComparison(
            outcome="differ",
            message="local build info is missing; order unknown",
        )
    local_version = str(local.get("version") or "")
    remote_version = str(remote.get("version") or "")
    local_display = str(local.get("display") or f"sase-listen {local_version}")
    remote_display = str(remote.get("display") or f"sase-listen {remote_version}")
    local_tuple = _parse_version_tuple(local_version)
    remote_tuple = _parse_version_tuple(remote_version)
    if local_tuple is None or remote_tuple is None:
        return BuildComparison(
            outcome="differ",
            message=(
                f"builds differ (order unknown): this machine runs"
                f" {local_display}; feed host runs {remote_display}"
            ),
        )
    if local_tuple > remote_tuple:
        return BuildComparison(
            outcome="remote_older",
            message=(
                f"feed host runs older {remote_display};"
                f" this machine runs {local_display}"
            ),
        )
    if local_tuple < remote_tuple:
        return BuildComparison(
            outcome="local_older",
            message=(
                f"this machine runs older {local_display};"
                f" feed host runs {remote_display}"
            ),
        )
    local_commit = str(local.get("commit") or "")
    remote_commit = str(remote.get("commit") or "")
    if local_commit and remote_commit and local_commit != remote_commit:
        return BuildComparison(
            outcome="differ",
            message=(
                f"builds differ: this machine runs {local_display};"
                f" feed host runs {remote_display}"
            ),
        )
    return BuildComparison(
        outcome="same",
        message=f"builds match: {local_display}",
    )
