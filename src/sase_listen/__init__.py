"""sase-listen: narrated, chaptered audio editions of Markdown."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("sase-listen")
except PackageNotFoundError:
    __version__ = "0.1.2"

__all__ = ["__version__"]
