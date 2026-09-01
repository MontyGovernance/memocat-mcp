"""Fail fast when MCPB release metadata or its final icon is incomplete."""

from __future__ import annotations

import json
import struct
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def project_version() -> str:
    # Avoid adding a TOML dependency to this release gate.
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"', 2)[1]
    raise SystemExit("pyproject.toml has no project version")


def png_shape(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SystemExit(f"{path.relative_to(ROOT)} is not a valid PNG")
    width, height, _depth, color_type = struct.unpack(">IIBB", data[16:26])
    return width, height, color_type


def main() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    registry = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    primary_plugin = json.loads(
        (ROOT / "plugins/montycat-mcp/.claude-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )
    legacy_plugin = json.loads(
        (ROOT / "plugins/memocat-mcp/.claude-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )
    legacy_project = tomllib.loads(
        (ROOT / "compat/memocat-mcp/pyproject.toml").read_text(encoding="utf-8")
    )
    expected = project_version()
    versions = {
        "manifest.json": manifest.get("version"),
        "server.json": registry.get("version"),
        "primary Claude plugin": primary_plugin.get("version"),
        "legacy Claude plugin": legacy_plugin.get("version"),
        "legacy PyPI package": legacy_project["project"].get("version"),
    }
    mismatches = {name: value for name, value in versions.items() if value != expected}
    if mismatches:
        raise SystemExit(f"release versions do not match {expected}: {mismatches}")

    if registry.get("name") != "io.github.MontyGovernance/montycat-mcp":
        raise SystemExit("server.json does not use the Montycat MCP registry identity")
    if primary_plugin.get("name") != "montycat-mcp":
        raise SystemExit("primary Claude plugin is not named montycat-mcp")
    dependency = f"montycat-mcp=={expected}"
    if dependency not in legacy_project["project"].get("dependencies", []):
        raise SystemExit(f"legacy PyPI package must depend on {dependency}")

    icon = ROOT / manifest["icon"]
    width, height, color_type = png_shape(icon)
    if (width, height) != (512, 512):
        raise SystemExit(
            f"{icon.relative_to(ROOT)} is {width}x{height}; final MCPB icon must be 512x512"
        )
    if color_type not in (4, 6):
        raise SystemExit(
            f"{icon.relative_to(ROOT)} lacks an alpha channel; use transparent RGBA/GA PNG"
        )

    print(f"MCPB release metadata ready: {manifest['name']}@{expected}")


if __name__ == "__main__":
    main()
