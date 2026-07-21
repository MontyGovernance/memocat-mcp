"""Engine bootstrap (AUTOSTART_PLAN.md) — no engine, no Docker, no network.

Downloads and process launches are mocked; the `file://` path is exercised for
real, which is also how tier 2 is developed before any artifact is published.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest

from memocat_mcp import bootstrap


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.montycat."""
    monkeypatch.setenv("MONTYCAT_HOME", str(tmp_path / "montycat"))
    for var in ("MONTYCAT_URI", "MEMOCAT_BINARY_URL", "MEMOCAT_AUTOSTART",
                "MONTYCAT_USERNAME", "MONTYCAT_PASSWORD", "MONTYCAT_HOST",
                "MONTYCAT_PORT", "MONTYCAT_STORE"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


# ── platform / URL resolution ────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Darwin", "arm64", "macos-universal"),
        ("Darwin", "x86_64", "macos-universal"),   # one universal artifact
        ("Linux", "x86_64", "linux-x86_64"),
        ("Windows", "AMD64", "windows-x86_64"),
        ("Linux", "armv7l", None),                 # no build published
    ],
)
def test_platform_slug(monkeypatch, system, machine, expected):
    monkeypatch.setattr(bootstrap.platform, "system", lambda: system)
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: machine)
    assert bootstrap.platform_slug() == expected


def test_unsupported_platform_has_no_url(monkeypatch):
    monkeypatch.setattr(bootstrap, "platform_slug", lambda: None)
    assert bootstrap.resolve_binary_url() is None


def test_binary_url_override_wins(monkeypatch):
    monkeypatch.setenv("MEMOCAT_BINARY_URL", "file:///tmp/engine.tar.gz")
    assert bootstrap.resolve_binary_url() == "file:///tmp/engine.tar.gz"


@pytest.mark.parametrize(
    ("machine", "tag"),
    [("arm64", "arm64-semantic"), ("aarch64", "arm64-semantic"),
     ("x86_64", "semantic"), ("AMD64", "semantic")],
)
def test_docker_tag_matches_architecture(monkeypatch, machine, tag):
    """Apple Silicon must not get the amd64 image — under emulation the
    embedding runtime's warm-up crashes."""
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: machine)
    assert bootstrap.docker_tag() == tag


# ── download + verification ──────────────────────────────────────────────────

def _make_archive(tmp_path: Path, name: str = "montycat_bin") -> Path:
    payload = io.BytesIO(b"#!/bin/sh\necho fake engine\n")
    archive = tmp_path / "engine.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo(name)
        info.size = len(payload.getvalue())
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(payload.getvalue()))
    return archive


@pytest.mark.asyncio
async def test_downloads_verifies_and_caches(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / "engine.tar.gz.sha256").write_text(f"{digest}  engine.tar.gz\n")
    monkeypatch.setenv("MEMOCAT_BINARY_URL", f"file://{archive}")

    binary = await bootstrap.download_binary()

    assert binary is not None and binary.exists()
    assert os.access(binary, os.X_OK), "downloaded engine must be executable"


@pytest.mark.asyncio
async def test_checksum_mismatch_refuses_to_run_it(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    (tmp_path / "engine.tar.gz.sha256").write_text("0" * 64 + "  engine.tar.gz\n")
    monkeypatch.setenv("MEMOCAT_BINARY_URL", f"file://{archive}")

    with pytest.raises(bootstrap.BootstrapError, match="checksum mismatch"):
        await bootstrap.download_binary()


@pytest.mark.asyncio
async def test_missing_checksum_is_refused_not_trusted(tmp_path, monkeypatch):
    """No published checksum means we cannot verify it, so we do not execute
    it — falling through to Docker is the safe outcome."""
    archive = _make_archive(tmp_path)
    monkeypatch.setenv("MEMOCAT_BINARY_URL", f"file://{archive}")
    assert await bootstrap.download_binary() is None


@pytest.mark.asyncio
async def test_unavailable_artifact_falls_through_quietly(tmp_path, monkeypatch):
    """Until the artifact is published the URL 404s; that is expected, not an
    error the user should see."""
    monkeypatch.setenv("MEMOCAT_BINARY_URL", f"file://{tmp_path}/does-not-exist.tar.gz")
    assert await bootstrap.download_binary() is None


@pytest.mark.asyncio
async def test_cached_binary_is_reused_without_downloading(tmp_path, monkeypatch):
    archive = _make_archive(tmp_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / "engine.tar.gz.sha256").write_text(f"{digest}\n")
    monkeypatch.setenv("MEMOCAT_BINARY_URL", f"file://{archive}")

    first = await bootstrap.download_binary()
    archive.unlink()  # source gone — a second fetch would now fail
    second = await bootstrap.download_binary()

    assert first == second, "second launch must reuse the cache"


def test_archive_cannot_escape_the_cache_directory(tmp_path):
    """A hostile archive must not write outside the cache."""
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        info = tarfile.TarInfo("../escaped")
        info.size = 0
        tf.addfile(info, io.BytesIO(b""))

    with pytest.raises(bootstrap.BootstrapError, match="escapes target"):
        bootstrap._unpack(evil, tmp_path / "cache")


# ── credentials ──────────────────────────────────────────────────────────────

def test_credentials_are_generated_once_and_reused():
    user, password, _ = bootstrap.credentials()
    again = bootstrap.credentials()
    assert (user, password) == (again[0], again[1]), \
        "a regenerated password would lock us out of the existing data volume"
    assert len(password) >= 16


def test_credentials_file_is_not_world_readable():
    bootstrap.credentials()
    path = bootstrap._home() / "memocat.json"
    assert path.stat().st_mode & 0o077 == 0, "password file must be 0600"


def test_explicit_credentials_are_respected(monkeypatch):
    monkeypatch.setenv("MONTYCAT_USERNAME", "alice")
    monkeypatch.setenv("MONTYCAT_PASSWORD", "hunter2")
    assert bootstrap.credentials()[:2] == ("alice", "hunter2")


# ── tier orchestration ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_existing_engine_short_circuits_everything(monkeypatch):
    monkeypatch.setattr(bootstrap, "probe", lambda *a, **k: True)

    async def _fail(*_a, **_k):
        pytest.fail("must not try to start anything when one is already running")

    monkeypatch.setattr(bootstrap, "start_native", _fail)
    monkeypatch.setattr(bootstrap, "start_docker", _fail)

    assert await bootstrap.ensure_engine() == "existing"


@pytest.mark.asyncio
async def test_autostart_off_refuses_with_instructions(monkeypatch):
    monkeypatch.setattr(bootstrap, "probe", lambda *a, **k: False)
    monkeypatch.setenv("MEMOCAT_AUTOSTART", "off")

    with pytest.raises(bootstrap.BootstrapError, match="MEMOCAT_AUTOSTART=off"):
        await bootstrap.ensure_engine()


@pytest.mark.asyncio
async def test_explicit_uri_is_never_second_guessed(monkeypatch):
    """If the user named an engine, starting a different local one would write
    memories somewhere they are not looking."""
    monkeypatch.setattr(bootstrap, "probe", lambda *a, **k: False)
    monkeypatch.setenv("MONTYCAT_URI", "montycat://u:p@10.0.0.5:21210/store")

    async def _fail(*_a, **_k):
        pytest.fail("must not start a local engine when MONTYCAT_URI is set")

    monkeypatch.setattr(bootstrap, "start_native", _fail)
    monkeypatch.setattr(bootstrap, "start_docker", _fail)

    with pytest.raises(bootstrap.BootstrapError, match="nothing is listening"):
        await bootstrap.ensure_engine()


@pytest.mark.asyncio
async def test_native_is_preferred_over_docker(monkeypatch):
    monkeypatch.setattr(bootstrap, "probe", lambda *a, **k: False)

    async def _native(*_a, **_k):
        return True

    async def _docker(*_a, **_k):
        pytest.fail("docker should not be reached when native succeeds")

    monkeypatch.setattr(bootstrap, "start_native", _native)
    monkeypatch.setattr(bootstrap, "start_docker", _docker)
    assert await bootstrap.ensure_engine() == "native"


@pytest.mark.asyncio
async def test_falls_through_to_docker_when_native_unavailable(monkeypatch):
    monkeypatch.setattr(bootstrap, "probe", lambda *a, **k: False)

    async def _native(*_a, **_k):
        return False

    async def _docker(*_a, **_k):
        return True

    monkeypatch.setattr(bootstrap, "start_native", _native)
    monkeypatch.setattr(bootstrap, "start_docker", _docker)
    assert await bootstrap.ensure_engine() == "docker"


@pytest.mark.asyncio
async def test_final_failure_names_both_install_paths(monkeypatch):
    monkeypatch.setattr(bootstrap, "probe", lambda *a, **k: False)

    async def _no(*_a, **_k):
        return False

    monkeypatch.setattr(bootstrap, "start_native", _no)
    monkeypatch.setattr(bootstrap, "start_docker", _no)

    with pytest.raises(bootstrap.BootstrapError) as excinfo:
        await bootstrap.ensure_engine()

    message = str(excinfo.value)
    assert "arm64-semantic" in message, "Apple Silicon users need the right tag"
    assert "montygovernance.com/download" in message
    assert "MEMOCAT_AUTOSTART=off" in message


@pytest.mark.asyncio
async def test_wait_until_ready_is_bounded(monkeypatch):
    """Never hang: a permanently-dead engine must give up, not block forever."""
    monkeypatch.setattr(bootstrap, "probe", lambda *a, **k: False)
    import asyncio

    loop = asyncio.get_running_loop()
    start = loop.time()
    ready = await bootstrap.wait_until_ready("127.0.0.1", 59997, deadline_sec=1)
    assert ready is False
    assert loop.time() - start < 5
