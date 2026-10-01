"""ffmpeg discovery and capability probing. Owner: audio phase."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass

from sase_listen.errors import ExitCode, SaseListenError

#: Environment override for the ffmpeg binary (highest precedence).
ENV_VAR = "SASE_LISTEN_FFMPEG"

_PROBE_TIMEOUT_S = 15


@dataclass(frozen=True)
class FfmpegInfo:
    """A resolved ffmpeg binary plus its mastering capabilities."""

    exe: str
    source: str
    has_libmp3lame: bool
    has_loudnorm: bool

    def describe(self) -> dict[str, str | bool]:
        """JSON-safe summary for `doctor` output."""
        return {
            "exe": self.exe,
            "source": self.source,
            "has_libmp3lame": self.has_libmp3lame,
            "has_loudnorm": self.has_loudnorm,
        }

    def require_mastering(self) -> None:
        """Raise a config error when the binary cannot master episodes."""
        missing: list[str] = []
        if not self.has_libmp3lame:
            missing.append("libmp3lame")
        if not self.has_loudnorm:
            missing.append("loudnorm")
        if missing:
            raise SaseListenError(
                f"ffmpeg at {self.exe} (source: {self.source}) "
                f"is missing {', '.join(missing)}; "
                "install a full ffmpeg build or rely on the bundled "
                "imageio-ffmpeg binary.",
                ExitCode.CONFIG,
                hint="Unset SASE_LISTEN_FFMPEG so sase-listen uses its bundle.",
            )


def _probe(exe: str) -> tuple[bool, bool]:
    """Check for the libmp3lame encoder and the loudnorm filter."""
    try:
        encoders = subprocess.run(
            [exe, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
        )
        filters = subprocess.run(
            [exe, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SaseListenError(
            f"Could not run ffmpeg at {exe}: {exc}",
            ExitCode.CONFIG,
            hint="Unset SASE_LISTEN_FFMPEG or install a working ffmpeg.",
        ) from exc
    if encoders.returncode != 0 or filters.returncode != 0:
        raise SaseListenError(
            f"Could not probe ffmpeg at {exe}.",
            ExitCode.CONFIG,
            hint="Unset SASE_LISTEN_FFMPEG or install a working ffmpeg.",
        )
    return ("libmp3lame" in encoders.stdout, " loudnorm " in filters.stdout)


@functools.lru_cache(maxsize=1)
def resolve_ffmpeg() -> FfmpegInfo:
    """Resolve the ffmpeg binary: env, then PATH, then bundled imageio-ffmpeg.

    The capability probe runs once and the result is cached; call
    ``resolve_ffmpeg.cache_clear()`` in tests that change the environment.
    """
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        if not (os.path.isfile(override) and os.access(override, os.X_OK)):
            raise SaseListenError(
                f"{ENV_VAR}={override} is not an executable file.",
                ExitCode.CONFIG,
                hint="Point it at an ffmpeg binary or unset it.",
            )
        lame, loud = _probe(override)
        return FfmpegInfo(
            exe=override,
            source=f"env:{ENV_VAR}",
            has_libmp3lame=lame,
            has_loudnorm=loud,
        )
    on_path = shutil.which("ffmpeg")
    if on_path:
        lame, loud = _probe(on_path)
        return FfmpegInfo(
            exe=on_path,
            source="PATH",
            has_libmp3lame=lame,
            has_loudnorm=loud,
        )
    from imageio_ffmpeg import get_ffmpeg_exe

    bundled = get_ffmpeg_exe()
    lame, loud = _probe(bundled)
    return FfmpegInfo(
        exe=bundled,
        source="imageio-ffmpeg",
        has_libmp3lame=lame,
        has_loudnorm=loud,
    )
