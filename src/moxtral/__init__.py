from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("moxtral")
except PackageNotFoundError:  # e.g. vendored source tree without installation
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
