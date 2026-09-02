"""Deprecated import compatibility for :mod:`montycat_mcp`."""

from importlib import import_module
import sys

from montycat_mcp import __version__

for _name in ("bootstrap", "server", "watch"):
    _module = import_module(f"montycat_mcp.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

__all__ = ["__version__", "bootstrap", "server", "watch"]
