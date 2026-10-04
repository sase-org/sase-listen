"""Private podcast feed for AntennaPod. Owner: feed phase.

Publishing copies an episode's MP3, cover, and chapters JSON from the
library into the served-only feed directory
(``$XDG_DATA_HOME/sase-listen/feed`` by default) and regenerates
``feed.xml`` atomically. Only what is published is ever served: the
library itself is never exposed.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import os
import secrets
import shlex
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import yaml

from sase_listen import __version__
from sase_listen.audio.cover import generate_title_card
from sase_listen.config import SaseListenConfig, load_config
from sase_listen.errors import ExitCode, SaseListenError
from sase_listen.library import episode_path, list_episode_ids, read_manifest
from sase_listen.paths import config_path, feed_dir_default, library_dir, locks_dir

#: Timeout for the external `token_command`, matching the engines contract.
TOKEN_COMMAND_TIMEOUT_S = 15

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"

CHAPTERS_MIME = "application/json+chapters"

EPISODES_SUBDIR = "episodes"
FEED_XML_NAME = "feed.xml"
CHANNEL_COVER_NAME = "cover.jpg"
RECEIVE_PROTOCOL = 1
FEED_LOCK_TIMEOUT_S = 120

_feed_lock_state = threading.local()
_feed_thread_lock = threading.Lock()


@dataclass
class FeedEpisode:
    """One published episode, read back from the feed directory."""

    episode_id: str
    manifest: dict[str, object]
    mp3_path: Path
    mp3_sha256: str
    created_at: datetime


def resolve_token(cfg: SaseListenConfig) -> str:
    """Return the feed token, never logging or echoing its value.

    The configured ``token`` wins; otherwise ``token_command`` runs without
    a shell (15 s timeout) and contributes its first output line.
    """
    direct = cfg.feed.token.strip()
    if direct:
        return direct
    command = cfg.feed.token_command.strip()
    if command:
        try:
            proc = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=TOKEN_COMMAND_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SaseListenError(
                "No feed token: token_command failed to run.",
                ExitCode.CONFIG,
                hint="Check feed.token_command, or run `sase-listen feed init`.",
            ) from exc
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.splitlines()[0].strip()
        raise SaseListenError(
            "No feed token: token_command produced no token.",
            ExitCode.CONFIG,
            hint="Check feed.token_command, or run `sase-listen feed init`.",
        )
    raise SaseListenError(
        "No feed token is configured.",
        ExitCode.CONFIG,
        hint="Run `sase-listen feed init --base-url URL` first.",
    )


def feed_root(cfg: SaseListenConfig, override: Path | None = None) -> Path:
    """Return the served-only feed directory for this config."""
    if override is not None:
        return override
    raw = cfg.feed.dir.strip()
    if raw:
        return Path(raw).expanduser()
    return feed_dir_default()


def require_base_url(cfg: SaseListenConfig) -> str:
    """Return the configured base URL, refusing when it is missing."""
    base = cfg.feed.base_url.strip().rstrip("/")
    if not base:
        raise SaseListenError(
            "No feed base_url is configured.",
            ExitCode.CONFIG,
            hint="Run `sase-listen feed init --base-url URL` first.",
        )
    return base


def subscribe_url(cfg: SaseListenConfig) -> str:
    """Return the private subscribe URL (base + token path + feed.xml)."""
    return f"{require_base_url(cfg)}/{resolve_token(cfg)}/{FEED_XML_NAME}"


def mask_token_in_url(url: str, token: str) -> str:
    """Replace the feed token in ``url`` with ``****``."""
    if token and token in url:
        return url.replace(token, "****")
    return url


def masked_subscribe_url(cfg: SaseListenConfig, *, show: bool = False) -> str:
    """Return the subscribe URL with the token masked unless asked to show."""
    url = subscribe_url(cfg)
    if show:
        return url
    return mask_token_in_url(url, resolve_token(cfg))


@contextmanager
def feed_lock(timeout_s: float = FEED_LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold an exclusive lock on the feed directory.

    ``fcntl.flock`` is per open file description, so nested acquisition in
    the same thread would deadlock. A thread-local depth counter makes the
    lock re-entrant: ``publish_episode`` may call ``rebuild_feed``.
    """
    depth = getattr(_feed_lock_state, "depth", 0)
    if depth:
        _feed_lock_state.depth = depth + 1
        try:
            yield
        finally:
            _feed_lock_state.depth = depth
        return
    if not _feed_thread_lock.acquire(timeout=timeout_s):
        raise SaseListenError(
            "the feed is busy.",
            ExitCode.UNEXPECTED,
            hint="Wait for the other publish to finish, then retry.",
        )
    lock_path = locks_dir() / "feed.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    deadline = time.monotonic() + timeout_s
    try:
        handle = lock_path.open("a+", encoding="utf-8")
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise SaseListenError(
                        "the feed is busy.",
                        ExitCode.UNEXPECTED,
                        hint="Wait for the other publish to finish, then retry.",
                    ) from None
                time.sleep(0.05)
        _feed_lock_state.depth = 1
        try:
            yield
        finally:
            _feed_lock_state.depth = 0
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if handle is not None:
            handle.close()
        _feed_thread_lock.release()


def funnel_command(feed_dir: Path, token: str) -> str:
    """Return the exact Tailscale Funnel command that serves this feed."""
    return f"tailscale funnel --bg --https=8443 --set-path=/{token} {feed_dir}"


def print_qr(url: str) -> str:
    """Render a terminal QR code for the subscribe URL."""
    import segno

    buf = io.StringIO()
    segno.make(url).terminal(out=buf, compact=True)
    return buf.getvalue()


def _parse_created_at(value: object, fallback: Path) -> datetime:
    """Parse a manifest ``created_at`` ISO string, falling back to mtime."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback.stat().st_mtime, tz=UTC)


def _read_feed_episode(episode_dir: Path, episode_id: str) -> FeedEpisode | None:
    """Read one published episode dir; None when it holds no manifest."""
    manifest_path = episode_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    import json as _json

    raw = _json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    manifest = dict(raw)
    mp3_name: object = None
    audio = manifest.get("audio")
    if isinstance(audio, dict):
        mp3_name = audio.get("file")
    candidates = (
        [str(mp3_name)] if isinstance(mp3_name, str) and mp3_name else ["episode.mp3"]
    )
    mp3s = sorted(episode_dir.glob("*.mp3"))
    mp3_path = episode_dir / candidates[0]
    if not mp3_path.is_file() and mp3s:
        mp3_path = mp3s[0]
    if not mp3_path.is_file():
        return None
    digest = hashlib.sha256(mp3_path.read_bytes()).hexdigest()
    return FeedEpisode(
        episode_id=episode_id,
        manifest=manifest,
        mp3_path=mp3_path,
        mp3_sha256=digest,
        created_at=_parse_created_at(manifest.get("created_at"), manifest_path),
    )


def list_feed_episodes(root: Path) -> list[FeedEpisode]:
    """Return published episodes, newest first by manifest ``created_at``."""
    episodes_dir = root / EPISODES_SUBDIR
    if not episodes_dir.is_dir():
        return []
    found: list[FeedEpisode] = []
    for child in sorted(episodes_dir.iterdir()):
        if not child.is_dir():
            continue
        entry = _read_feed_episode(child, child.name)
        if entry is not None:
            found.append(entry)
    found.sort(key=lambda item: item.created_at, reverse=True)
    return found


def apply_retention(
    episodes: list[FeedEpisode],
    *,
    retention_days: int,
    max_episodes: int,
    now: datetime,
    root: Path,
) -> list[str]:
    """Delete feed-only episode dirs outside retention; return removed ids.

    Episodes older than ``retention_days`` (by manifest ``created_at``) go
    first, then the oldest beyond ``max_episodes``. The library is untouched.
    """
    keep: list[FeedEpisode] = []
    drop: list[FeedEpisode] = []
    for entry in episodes:
        age_days = (now - entry.created_at).total_seconds() / 86400
        if retention_days >= 0 and age_days > retention_days:
            drop.append(entry)
        else:
            keep.append(entry)
    # `episodes` arrives newest-first, so the tail is the oldest.
    if max_episodes >= 0 and len(keep) > max_episodes:
        drop.extend(keep[max_episodes:])
        keep = keep[:max_episodes]
    removed: list[str] = []
    for entry in drop:
        shutil.rmtree(root / EPISODES_SUBDIR / entry.episode_id, ignore_errors=True)
        removed.append(entry.episode_id)
    return removed


def source_url_for(source: object, templates: dict[str, str]) -> str:
    """Link a manifest ``source`` (``{"ref": ...}``) to the written report."""
    if not isinstance(source, dict):
        return ""
    ref = source.get("ref")
    if not isinstance(ref, str) or ":" not in ref:
        return ""
    kind, _, path = ref.partition(":")
    template = templates.get(kind)
    if not template:
        return ""
    try:
        return template.format(path=path)
    except (KeyError, IndexError, ValueError):
        return ""


def _mmss(total_s: float) -> str:
    total = max(0, round(total_s))
    return f"{total // 60}:{total % 60:02d}"


def _description_html(
    title: str, chapters: list[dict[str, Any]], source_url: str
) -> str:
    parts = [f"<p>AI-narrated audio edition of {escape(title)}.</p>"]
    if chapters:
        items = "".join(
            f"<li>{_mmss(float(ch.get('start_ms', 0)) / 1000)} "
            f"\u2014 {escape(str(ch.get('title', '')))}</li>"
            for ch in chapters
        )
        parts.append(f"<h3>Chapters</h3><ul>{items}</ul>")
    if source_url:
        parts.append(
            f'<p><a href="{escape(source_url)}">Read the written report</a></p>'
        )
    return "".join(parts)


def build_feed_xml(
    *,
    cfg: SaseListenConfig,
    episodes: list[FeedEpisode],
    token: str,
    now: datetime,
) -> bytes:
    """Build the RSS 2.0 + iTunes + Podcasting 2.0 feed document."""
    base = cfg.feed.base_url.strip().rstrip("/")
    prefix = f"{base}/{token}"
    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("podcast", PODCAST_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    title = cfg.feed.title.strip() or "SASE Listen"

    def text(parent: ET.Element, tag: str, value: str) -> ET.Element:
        child = ET.SubElement(parent, tag)
        child.text = value
        return child

    text(channel, "title", title)
    text(channel, "description", cfg.feed.description)
    text(channel, "language", cfg.feed.language or "en")
    text(channel, "link", f"{prefix}/")
    author = cfg.feed.author.strip()
    if author:
        text(channel, "managingEditor", author)
        text(channel, f"{{{ITUNES_NS}}}author", author)
    text(channel, f"{{{ITUNES_NS}}}block", "yes")
    text(channel, f"{{{PODCAST_NS}}}locked", "yes")
    text(channel, f"{{{ITUNES_NS}}}explicit", "false")
    ET.SubElement(
        channel,
        f"{{{ITUNES_NS}}}image",
        {"href": f"{prefix}/{CHANNEL_COVER_NAME}"},
    )
    image = ET.SubElement(channel, "image")
    text(image, "url", f"{prefix}/{CHANNEL_COVER_NAME}")
    text(image, "title", title)
    text(image, "link", f"{prefix}/")
    text(channel, "lastBuildDate", format_datetime(now))

    for entry in episodes:
        manifest = entry.manifest
        item_title = str(manifest.get("title", entry.episode_id))
        item = ET.SubElement(channel, "item")
        text(item, "title", item_title)
        audio_raw = manifest.get("audio")
        audio: dict[str, Any] = dict(audio_raw) if isinstance(audio_raw, dict) else {}
        duration_s = float(audio.get("duration_s", 0) or 0)
        size = entry.mp3_path.stat().st_size
        chapters_raw = manifest.get("chapters")
        chapters: list[dict[str, Any]] = (
            [dict(c) for c in chapters_raw] if isinstance(chapters_raw, list) else []
        )
        source_url = source_url_for(
            manifest.get("source"), cfg.feed.source_url_templates
        )
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = f"{entry.episode_id}@{entry.mp3_sha256[:8]}"
        text(item, "pubDate", format_datetime(entry.created_at))
        desc = ET.SubElement(item, "description")
        desc.text = _description_html(item_title, chapters, source_url)
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": f"{prefix}/{EPISODES_SUBDIR}/"
                f"{entry.episode_id}/{entry.mp3_path.name}",
                "length": str(size),
                "type": "audio/mpeg",
            },
        )
        text(item, f"{{{ITUNES_NS}}}duration", str(round(duration_s)))
        ET.SubElement(
            item,
            f"{{{ITUNES_NS}}}image",
            {"href": f"{prefix}/{EPISODES_SUBDIR}/{entry.episode_id}/cover.jpg"},
        )
        text(item, f"{{{ITUNES_NS}}}episodeType", "full")
        ET.SubElement(
            item,
            f"{{{PODCAST_NS}}}chapters",
            {
                "url": f"{prefix}/{EPISODES_SUBDIR}/{entry.episode_id}/chapters.json",
                "type": CHAPTERS_MIME,
            },
        )
    body: bytes = ET.tostring(rss, encoding="utf-8")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{os.getpid()}-{path.name}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def rebuild_feed(
    cfg: SaseListenConfig,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply retention and regenerate ``feed.xml`` + channel art atomically.

    Returns ``{"episodes": [...ids...], "removed": [...], "feed_xml": str}``.
    """
    with feed_lock():
        feed_dir = feed_root(cfg, root)
        moment = now if now is not None else datetime.now(UTC)
        episodes = list_feed_episodes(feed_dir)
        removed = apply_retention(
            episodes,
            retention_days=cfg.feed.retention_days,
            max_episodes=cfg.feed.max_episodes,
            now=moment,
            root=feed_dir,
        )
        kept = [e for e in episodes if e.episode_id not in set(removed)]
        token = resolve_token(cfg)
        _atomic_write(
            feed_dir / CHANNEL_COVER_NAME,
            generate_title_card(cfg.feed.title.strip() or "SASE Listen"),
        )
        _atomic_write(
            feed_dir / FEED_XML_NAME,
            build_feed_xml(cfg=cfg, episodes=kept, token=token, now=moment),
        )
        return {
            "episodes": [e.episode_id for e in kept],
            "removed": removed,
            "feed_xml": str(feed_dir / FEED_XML_NAME),
        }


def resolve_episode_ref(
    ref: str,
    *,
    latest: bool = False,
    library: Path | None = None,
) -> str:
    """Resolve an episode id, MP3 path, or ``--latest`` to an episode id."""
    root = library if library is not None else library_dir()
    if latest:
        ids = list_episode_ids(root)
        if not ids:
            raise SaseListenError(
                "The library has no episodes yet.",
                ExitCode.USAGE,
                hint="Render one first with `sase-listen render`.",
            )
        by_time = sorted(
            ids,
            key=lambda eid: (root / eid / "manifest.json").stat().st_mtime,
        )
        return by_time[-1]
    candidate = Path(ref).expanduser()
    if candidate.exists():
        if candidate.is_dir() and (candidate / "manifest.json").is_file():
            return candidate.name
        if candidate.is_file() and candidate.parent.name:
            parent = candidate.parent
            if (parent / "manifest.json").is_file():
                return parent.name
        raise SaseListenError(
            f"Not a library episode: {ref}.",
            ExitCode.USAGE,
            hint="Pass an episode id, an episode MP3 path, or --latest.",
        )
    try:
        read_manifest(ref, root)
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise SaseListenError(
            f"Unknown episode: {ref}.",
            ExitCode.USAGE,
            hint="Pass an episode id, an episode MP3 path, or --latest.",
        ) from exc
    return ref


def publish_episode(
    episode_id: str,
    cfg: SaseListenConfig,
    *,
    library: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Copy an episode into the feed dir and regenerate ``feed.xml``."""
    require_base_url(cfg)
    resolve_token(cfg)  # Fail before copying when the token is missing.
    lib = library if library is not None else library_dir()
    src = episode_path(episode_id, lib)
    manifest = read_manifest(episode_id, lib)
    audio = manifest.get("audio")
    mp3_name = (
        audio.get("file")
        if isinstance(audio, dict) and isinstance(audio.get("file"), str)
        else None
    )
    mp3s = sorted(src.glob("*.mp3"))
    src_mp3 = src / mp3_name if mp3_name else (mp3s[0] if mp3s else src / "ep.mp3")
    if not src_mp3.is_file():
        raise SaseListenError(
            f"Episode '{episode_id}' has no MP3 in the library.",
            ExitCode.UNEXPECTED,
            hint="Re-render the episode, then publish again.",
        )
    with feed_lock():
        feed_dir = feed_root(cfg, root)
        dest = feed_dir / EPISODES_SUBDIR / episode_id
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_mp3, dest / src_mp3.name)
        for name in ("cover.jpg", "chapters.json", "manifest.json"):
            candidate = src / name
            if candidate.is_file():
                shutil.copyfile(candidate, dest / name)
        rebuilt = rebuild_feed(cfg, root=feed_dir)
    token = resolve_token(cfg)
    base = cfg.feed.base_url.strip().rstrip("/")
    return {
        "episode_id": episode_id,
        "item_url": f"{base}/{token}/{EPISODES_SUBDIR}/{episode_id}/{src_mp3.name}",
        "episodes": rebuilt["episodes"],
        "removed": rebuilt["removed"],
    }


def unpublish_episode(
    episode_id: str,
    cfg: SaseListenConfig,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Remove an episode from the feed dir and regenerate ``feed.xml``."""
    with feed_lock():
        feed_dir = feed_root(cfg, root)
        target = feed_dir / EPISODES_SUBDIR / episode_id
        if not target.is_dir():
            raise SaseListenError(
                f"Episode '{episode_id}' is not published.",
                ExitCode.USAGE,
                hint="Check the id with `sase-listen feed`.",
            )
        shutil.rmtree(target)
        rebuilt = rebuild_feed(cfg, root=feed_dir)
    return {"episode_id": episode_id, **rebuilt}


def init_feed(
    base_url: str,
    *,
    print_only: bool = False,
    title: str = "",
) -> dict[str, Any]:
    """Generate a token, create the feed dir, and store (or print) config.

    Returns ``{"feed_dir", "subscribe_url", "funnel_command", ...}``.
    With ``print_only``, nothing is written: the ``feed:`` YAML snippet is
    returned for chezmoi-managed configs instead.
    """
    cfg, _ = load_config()
    base = base_url.strip().rstrip("/")
    if not base:
        raise SaseListenError(
            "A --base-url is required.",
            ExitCode.USAGE,
            hint="Example: sase-listen feed init --base-url https://host:8443",
        )
    try:
        existing = resolve_token(cfg)
    except SaseListenError:
        existing = ""
    token = existing or secrets.token_urlsafe(24)
    feed_dir = feed_root(cfg)
    label = (title or cfg.feed.title or "SASE Listen").strip()
    snippet = (
        "feed:\n"
        f"  dir: {feed_dir}\n"
        f"  base_url: {base}\n"
        f"  token: {token}\n"
        f"  title: {label}\n"
    )
    wrote = ""
    if print_only:
        pass
    else:
        feed_dir.mkdir(parents=True, exist_ok=True)
        target = config_path()
        raw: dict[str, Any] = {}
        if target.exists():
            loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                raw = dict(loaded)
        section = dict(raw.get("feed", {}) or {})
        section.setdefault("dir", str(feed_dir))
        section["base_url"] = base
        section["token"] = token
        if title:
            section["title"] = title
        raw["feed"] = section
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, yaml.safe_dump(raw, sort_keys=True).encode("utf-8"))
        wrote = str(target)
    url = f"{base}/{token}/{FEED_XML_NAME}"
    return {
        "feed_dir": str(feed_dir),
        "subscribe_url": url,
        "funnel_command": funnel_command(feed_dir, token),
        "token_reused": bool(existing),
        "config_path": wrote,
        "snippet": snippet if print_only else "",
    }


def mark_manifest_published(episode_id: str, library: Path | None = None) -> None:
    """Flip a committed manifest's ``published`` flag to true, atomically."""
    import json as _json

    lib = library if library is not None else library_dir()
    path = episode_path(episode_id, lib) / "manifest.json"
    raw = _json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return
    raw["published"] = True
    _atomic_write(path, (_json.dumps(raw, indent=2, sort_keys=True) + "\n").encode())


def feed_status(
    cfg: SaseListenConfig,
    *,
    root: Path | None = None,
    show_url: bool = False,
) -> dict[str, Any]:
    """Summarize the feed: URL, episode count, size, last build, retention."""
    feed_dir = feed_root(cfg, root)
    episodes = list_feed_episodes(feed_dir)
    size = (
        sum(f.stat().st_size for f in feed_dir.rglob("*") if f.is_file())
        if feed_dir.is_dir()
        else 0
    )
    xml_path = feed_dir / FEED_XML_NAME
    last_build = ""
    if xml_path.is_file():
        last_build = datetime.fromtimestamp(xml_path.stat().st_mtime, tz=UTC).isoformat(
            timespec="seconds"
        )
    try:
        url = masked_subscribe_url(cfg, show=show_url)
        configured = True
    except SaseListenError:
        url = ""
        configured = False
    from sase_listen.feedhost import local_hostname

    return {
        "feed_dir": str(feed_dir),
        "url": url,
        "url_masked": bool(url) and not show_url,
        "configured": configured,
        "episodes": len(episodes),
        "episode_ids": [e.episode_id for e in episodes],
        "size_bytes": size,
        "last_build": last_build,
        "retention": {
            "retention_days": cfg.feed.retention_days,
            "max_episodes": cfg.feed.max_episodes,
        },
        "host": local_hostname(),
        "sase_listen_version": __version__,
        "receive_protocol": RECEIVE_PROTOCOL,
    }
