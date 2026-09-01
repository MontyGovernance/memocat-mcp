"""Fail fast when MCPB release metadata or its final icon is incomplete."""

from __future__ import annotations

import json
import struct
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
    expected = project_version()
    if manifest.get("version") != expected:
        raise SystemExit(
            f"manifest version {manifest.get('version')!r} != project version {expected!r}"
        )

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
