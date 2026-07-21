"""MemoCat — MCP server exposing Montycat as self-hosted AI-agent memory."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth is pyproject.toml; reading it back avoids a second
    # number that silently drifts — this file said 0.1.0 while the package was
    # already 0.3.0.
    __version__ = version("memocat-mcp")
except PackageNotFoundError:  # source checkout, not installed
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
