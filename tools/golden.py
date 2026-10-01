"""Regenerate golden fixtures. Owner: script phase."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sase_listen.normalize import normalize_markdown

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "markdown"


def regenerate(fixture_dir: Path = FIXTURE_DIR) -> list[str]:
    """Regenerate every golden fixture; return the regenerated stem names."""
    done: list[str] = []
    for source in sorted(fixture_dir.glob("*.md")):
        if source.name.endswith(".narration.md"):
            continue
        text = source.read_text(encoding="utf-8")
        script_md, omissions = normalize_markdown(text, filename=source.name)
        stem = source.stem
        (fixture_dir / f"{stem}.narration.md").write_text(script_md, encoding="utf-8")
        payload = [o.to_dict() for o in omissions]
        (fixture_dir / f"{stem}.omissions.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        done.append(stem)
    return done


def main() -> int:
    """Regenerate golden fixtures and report what changed."""
    parser = argparse.ArgumentParser(description="Regenerate script golden fixtures.")
    parser.add_argument(
        "--dir", default=str(FIXTURE_DIR), help="Fixture directory to regenerate."
    )
    args = parser.parse_args()
    done = regenerate(Path(args.dir))
    if done:
        print(f"Regenerated {len(done)} fixture(s): {', '.join(done)}.")
    else:
        print("No golden fixtures yet (owner: script phase).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
