"""MemoCat — shared, persistent memory across MCP-compatible AI systems."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Keep pyproject.toml as the single version source.
    __version__ = version("memocat-mcp")
except PackageNotFoundError:  # source checkout, not installed
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
