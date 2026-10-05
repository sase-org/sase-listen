"""Minimal valid PDF builder in pure Python (no fixtures committed)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PdfTextRun:
    """One text line placed at an absolute position."""

    text: str
    size: float = 9.0
    bold: bool = False
    x: float = 72.0
    y: float = 720.0


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(
    pages: list[list[PdfTextRun]],
    *,
    info: dict[str, str] | None = None,
    outline: list[tuple[int, str]] | None = None,
) -> bytes:
    """Build a minimal multi-page PDF with Helvetica text and an xref table."""

    def _content(runs: list[PdfTextRun]) -> bytes:
        parts: list[str] = []
        for run in runs:
            font = "/F2" if run.bold else "/F1"
            parts.append(
                f"BT {font} {run.size:g} Tf {run.x:g} {run.y:g} Td "
                f"({_escape(run.text)}) Tj ET"
            )
        return ("\n".join(parts) + "\n").encode("latin-1")

    n_pages = len(pages)
    # Object numbering: 1 catalog, 2 pages, then per page (page, content),
    # then fonts, info, outlines.
    page_ids: list[int] = []
    content_ids: list[int] = []
    next_id = 3
    for _ in pages:
        page_ids.append(next_id)
        content_ids.append(next_id + 1)
        next_id += 2
    font_id = next_id
    helv_bold_id = next_id + 1
    next_id += 2
    info_id = next_id if info else 0
    if info:
        next_id += 1
    outline_ids: list[int] = []
    outlines_root_id = 0
    if outline:
        outlines_root_id = next_id
        next_id += 1
        outline_ids = list(range(next_id, next_id + len(outline)))
        next_id += len(outline)
    total = next_id - 1

    catalog = (
        "<< /Type /Catalog /Pages 2 0 R"
        + (f" /Outlines {outlines_root_id} 0 R" if outline else "")
        + " >>"
    )
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    pages_obj = f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>"

    bodies: dict[int, bytes] = {1: catalog.encode("latin-1"), 2: pages_obj.encode()}
    streams: dict[int, bytes] = {}
    for pid, cid, runs in zip(page_ids, content_ids, pages, strict=True):
        bodies[pid] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R "
            f"/F2 {helv_bold_id} 0 R >> >> /Contents {cid} 0 R >>"
        ).encode("latin-1")
        streams[cid] = _content(runs)
    bodies[font_id] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".encode(
        "latin-1"
    )
    bodies[helv_bold_id] = (
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>".encode("latin-1")
    )
    if info:
        entries = " ".join(f"/{key} ({_escape(value)})" for key, value in info.items())
        bodies[info_id] = f"<< {entries} >>".encode("latin-1")
    if outline:
        assert outlines_root_id
        first, last = outline_ids[0], outline_ids[-1]
        bodies[outlines_root_id] = (
            f"<< /Type /Outlines /First {first} 0 R /Last {last} 0 R "
            f"/Count {len(outline_ids)} >>"
        ).encode("latin-1")
        # Parent/child links for nesting levels.
        stack: list[int] = []
        for pos, ((level, title), oid) in enumerate(
            zip(outline, outline_ids, strict=True)
        ):
            while len(stack) >= level:
                stack.pop()
            parent = stack[-1] if stack else outlines_root_id
            # Siblings: previous outline at the same level.
            prev = 0
            for back in range(pos - 1, -1, -1):
                if outline[back][0] == level:
                    prev = outline_ids[back]
                    break
                if outline[back][0] < level:
                    break
            nxt = 0
            for fwd in range(pos + 1, len(outline)):
                if outline[fwd][0] == level:
                    nxt = outline_ids[fwd]
                    break
                if outline[fwd][0] < level:
                    break
            # Children: next outline at a deeper level before a dedent.
            children = [
                outline_ids[fwd]
                for fwd in range(pos + 1, len(outline))
                if outline[fwd][0] == level + 1
                and all(o[0] > level for o in outline[pos + 1 : fwd + 1])
            ]
            dest_page = page_ids[0]
            item = (
                f"<< /Title ({_escape(title)}) /Parent {parent} 0 R "
                f"/Dest [{dest_page} 0 R /Fit]"
            )
            if prev:
                item += f" /Prev {prev} 0 R"
            if nxt:
                item += f" /Next {nxt} 0 R"
            if children:
                count = 0
                scan = pos + 1
                while scan < len(outline) and outline[scan][0] > level:
                    count += 1
                    scan += 1
                item += (
                    f" /First {children[0]} 0 R /Last {children[-1]} 0 R /Count {count}"
                )
            item += " >>"
            bodies[oid] = item.encode("latin-1")
            stack.append(oid)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for oid in range(1, total + 1):
        offsets[oid] = len(out)
        if oid in streams:
            data = streams[oid]
            header = f"{oid} 0 obj\n<< /Length {len(data)} >>\nstream\n".encode(
                "latin-1"
            )
            out += header + data + b"endstream\nendobj\n"
            continue
        body = bodies[oid]
        out += f"{oid} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {total + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for oid in range(1, total + 1):
        out += f"{offsets[oid]:010d} 00000 n \n".encode("latin-1")
    trailer = f"<< /Size {total + 1} /Root 1 0 R"
    if info:
        trailer += f" /Info {info_id} 0 R"
    trailer += " >>"
    out += f"trailer\n{trailer}\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    return bytes(out)
