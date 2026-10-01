"""Allow `python -m sase_listen` to work."""

from sase_listen.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
