"""MCPB metadata that can be verified before the final artwork is supplied."""

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mcpb_manifest_matches_project_and_bundle_files():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == "0.4"
    assert manifest["version"] == project["project"]["version"]
    assert manifest["server"]["type"] == "uv"
    assert (ROOT / manifest["server"]["entry_point"]).is_file()
    assert (ROOT / manifest["icon"]).is_file()
    assert (ROOT / "PRIVACY.md").is_file()
    assert manifest["privacy_policies"]


def test_manifest_declares_exactly_the_runtime_tools():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    declared = {tool["name"] for tool in manifest["tools"]}

    # Import locally so this file remains a cheap metadata test at collection.
    from memocat_mcp.server import mcp

    runtime = set(mcp._tool_manager._tools)
    assert declared == runtime
