"""Governed-record engine: retrieval, authoring, validation, catalogs.

The import name and the distribution name are both `radialrig`.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("radialrig")
except PackageNotFoundError:  # running from a source tree, uninstalled
    __version__ = "0+unknown"

__all__ = ["__version__"]
