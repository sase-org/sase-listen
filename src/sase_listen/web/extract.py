"""Article extraction and outline repair for fetched HTML."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lxml import html as lxml_html

from sase_listen.errors import ExitCode, SaseListenError

_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_BYLINE_RE = re.compile(r"^By ([A-Z][^,\n]{1,60})", re.MULTILINE)
_MARKDOWN_DECORATION_RE = re.compile(r"(?:\*\*|__|\*|_|~~|`+)")
_SPACE_RE = re.compile(r"\s+")
_EXCLUDED = {"nav", "header", "footer", "aside", "button"}


@dataclass(frozen=True)
class Article:
    """Extracted article text and diagnostics."""

    markdown: str
    title: str
    author: str
    date: str
    site: str
    canonical_url: str
    description: str
    words: int
    outline: list[str]
    restored_headings: list[str]
    missing_headings: list[str]


def normalize_url(url: str) -> str:
    """Normalize a URL for source identity and strip common tracking keys."""
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if scheme not in {"http", "https"} or not host:
        raise SaseListenError(
            f"Not an http(s) URL: {url}.",
            ExitCode.USAGE,
            hint="Pass a URL beginning with http:// or https://.",
        )
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    netloc = f"{userinfo}{host.lower()}"
    if parsed.port is not None:
        netloc += f":{parsed.port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_KEYS
    ]
    return urlunsplit((scheme, netloc, parsed.path or "/", urlencode(query), ""))


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _MARKDOWN_DECORATION_RE.sub("", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+|>\s*)", "", text)
    return _SPACE_RE.sub(" ", text).strip()


def _excluded_element(element: object, scope: object) -> bool:
    node = element
    while node is not None and node is not scope:
        tag = str(getattr(node, "tag", "")).lower()
        role = str(getattr(node, "get", lambda *_: "")("role", "")).casefold()
        if tag in _EXCLUDED or role == "navigation":
            return True
        node = getattr(node, "getparent", lambda: None)()
    return False


def _outline(html_text: str) -> list[tuple[str, str, str]]:
    """Return (tag, heading, following prose) for main article headings."""
    try:
        root = lxml_html.fromstring(html_text)
    except (ValueError, TypeError):
        return []
    scope = (
        root.xpath(".//article[1]")
        or root.xpath(".//main[1]")
        or root.xpath(".//body[1]")
        or [root]
    )
    container = scope[0]
    nodes = list(container.iter())
    result: list[tuple[str, str, str]] = []
    for index, node in enumerate(nodes):
        tag = str(node.tag).lower()
        if tag not in {"h2", "h3"} or _excluded_element(node, container):
            continue
        heading = _SPACE_RE.sub(" ", node.text_content()).strip()
        if not heading:
            continue
        following = ""
        for candidate in nodes[index + 1 :]:
            candidate_tag = str(candidate.tag).lower()
            if candidate_tag not in {"p", "li", "blockquote"}:
                continue
            if _excluded_element(candidate, container):
                continue
            text = _SPACE_RE.sub(" ", candidate.text_content()).strip()
            if len(text) >= 40:
                following = text
                break
        result.append((tag, heading, following))
    return result


def repair_outline(markdown: str, html_text: str) -> tuple[str, list[str], list[str]]:
    """Restore missing H2/H3 headings before their matching extracted prose."""
    entries = _outline(html_text)
    lines = markdown.splitlines()
    restored: list[str] = []
    missing: list[str] = []
    search_from = 0
    for tag, heading, following in entries:
        normalized_heading = _normalize_text(heading)
        existing = any(
            re.match(r"^#{1,6}\s+", line)
            and _normalize_text(line) == normalized_heading
            for line in lines
        )
        if existing:
            continue
        normalized_following = _normalize_text(following)
        if len(normalized_following) < 40:
            missing.append(heading)
            continue
        prefix = normalized_following[:80]
        match_index = next(
            (
                i
                for i in range(search_from, len(lines))
                if _normalize_text(lines[i]).startswith(prefix)
            ),
            None,
        )
        if match_index is None:
            missing.append(heading)
            continue
        level = "##" if tag == "h2" else "###"
        lines.insert(match_index, f"{level} {heading}")
        restored.append(heading)
        search_from = match_index + 1
    return "\n".join(lines).strip() + "\n", restored, missing


def _metadata_value(metadata: object, name: str) -> str:
    value = getattr(metadata, name, "") if metadata is not None else ""
    return str(value).strip() if value else ""


def _fallback_title(html_text: str, url: str) -> str:
    try:
        root = lxml_html.fromstring(html_text)
        values = root.xpath("//h1[1]//text()") or root.xpath("//title[1]//text()")
        title = _SPACE_RE.sub(" ", " ".join(values)).strip()
        if title:
            return title
    except (ValueError, TypeError):
        pass
    return urlsplit(url).path.rstrip("/").split("/")[-1] or urlsplit(url).netloc


def _extract_byline(description: str, markdown: str) -> tuple[str, str]:
    description_match = _BYLINE_RE.search(description)
    if description_match:
        return description_match.group(1).strip(), markdown
    paragraphs = re.split(r"\n\s*\n", markdown)
    for index, paragraph in enumerate(paragraphs[:4]):
        match = _BYLINE_RE.match(paragraph.strip())
        if match:
            del paragraphs[index]
            return match.group(1).strip(), "\n\n".join(paragraphs).strip()
    return "", markdown


def extract_article(html_text: str, url: str) -> Article:
    """Extract an article, preserving article-section headings lost by parsers."""
    from trafilatura import extract, extract_metadata

    metadata = extract_metadata(html_text, default_url=url)
    markdown = extract(
        html_text,
        url=url,
        output_format="markdown",
        include_formatting=True,
        include_tables=True,
        include_links=False,
        include_images=False,
        include_comments=False,
        with_metadata=False,
    )
    if not markdown:
        markdown = ""
    title = _metadata_value(metadata, "title") or _fallback_title(html_text, url)
    description = _metadata_value(metadata, "description")
    author = _metadata_value(metadata, "author")
    if not author:
        author, _ = _extract_byline(description, "")
    body_author, markdown = _extract_byline("", markdown)
    author = author or body_author
    date = _metadata_value(metadata, "date")
    site = _metadata_value(metadata, "sitename")
    metadata_url = _metadata_value(metadata, "url")
    canonical = normalize_url(metadata_url or url)

    markdown = markdown.strip()
    first, separator, rest = markdown.partition("\n")
    if first.lstrip("# ").strip().casefold() == title.casefold():
        markdown = rest.lstrip("\n") if separator else ""
    markdown, restored, missing = repair_outline(markdown, html_text)
    word_count = len(markdown.split())
    if word_count < 150:
        raise SaseListenError(
            f"The extracted page is too short ({word_count} words).",
            ExitCode.UNEXPECTED,
            hint=(
                "Use --html for a saved page and check for a login, abstract, "
                "or error page."
            ),
        )
    outline = [heading for _, heading, _ in _outline(html_text)]
    return Article(
        markdown=markdown,
        title=title,
        author=author,
        date=date,
        site=site,
        canonical_url=canonical,
        description=description,
        words=word_count,
        outline=outline,
        restored_headings=restored,
        missing_headings=missing,
    )
