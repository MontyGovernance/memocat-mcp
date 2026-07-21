"""Zero-config engine bootstrap (AUTOSTART_PLAN.md).

`uvx memocat-mcp` should work on a machine with no engine and no configuration.
Four tiers, tried in order, first success wins:

  1. an engine is already reachable (or MONTYCAT_URI is set) -> just use it
  2. a native binary -> download, verify, cache under ~/.montycat/bin, run
  3. Docker -> pull and run the architecture-correct image
  4. neither -> one clear error naming both install paths; never hang

Two things here are deliberate and easy to "fix" wrongly:

* **The engine outlives this process.** Normally you would tear down a
  subprocess you spawned. This is a memory product — killing the database when
  the agent restarts would look like amnesia. It is left running and reused.
* **Downloads use stdlib only.** A `uvx`-installed tool pays for every
  dependency at install time, and an HTTP client is not worth that for one
  file fetch.

Nothing here runs when `MONTYCAT_URI` is set or an engine is already listening,
so operators who manage their own engine never trigger a download.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 21210
DOWNLOAD_BASE = "https://downloads.montygovernance.com/bin"
CONTAINER_NAME = "memocat-engine"
DEFAULT_STORE = "memocat"

logger = __import__("logging").getLogger("memocat.bootstrap")


class BootstrapError(RuntimeError):
    """No engine could be reached or started."""


# ── environment ──────────────────────────────────────────────────────────────

def _home() -> Path:
    return Path(os.environ.get("MONTYCAT_HOME", Path.home() / ".montycat"))


def _mode() -> str:
    """`auto` | `off` | `native` | `docker`."""
    return os.environ.get("MEMOCAT_AUTOSTART", "auto").strip().lower()


def _timeout() -> int:
    """Readiness budget. A cold semantic start downloads an embedding model
    before it serves, so this is minutes, not seconds."""
    try:
        return int(os.environ.get("MEMOCAT_ENGINE_TIMEOUT", "120"))
    except ValueError:
        return 120


def _host_port() -> tuple[str, int]:
    uri = os.environ.get("MONTYCAT_URI")
    if uri:
        try:
            hostport = uri.split("@", 1)[1].split("/", 1)[0]
            host, port = hostport.rsplit(":", 1)
            return host, int(port)
        except (IndexError, ValueError):
            pass
    return (
        os.environ.get("MONTYCAT_HOST", DEFAULT_HOST),
        int(os.environ.get("MONTYCAT_PORT", str(DEFAULT_PORT))),
    )


# ── tier 1: is something already there? ──────────────────────────────────────

def probe(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def wait_until_ready(host: str, port: int, deadline_sec: int) -> bool:
    """Poll until the engine answers or the budget runs out.

    Deliberately patient: the first semantic start fetches an embedding model
    (tens of megabytes) before it listens, and a short timeout would declare a
    perfectly healthy engine dead.
    """
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_sec
    delay = 0.25
    while loop.time() < end:
        if await asyncio.to_thread(probe, host, port, 1.0):
            return True
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 3.0)
    return False


# ── credentials ──────────────────────────────────────────────────────────────

def credentials() -> tuple[str, str, str]:
    """Superowner credentials for an engine we start ourselves.

    Generated once and persisted, because per-scope keyspace auto-provisioning
    needs superowner rights, and because a regenerated password would lock us
    out of the data volume on the next launch.
    """
    env_user = os.environ.get("MONTYCAT_USERNAME")
    env_pass = os.environ.get("MONTYCAT_PASSWORD")
    store = os.environ.get("MONTYCAT_STORE", DEFAULT_STORE)
    if env_user and env_pass:
        return env_user, env_pass, store

    path = _home() / "memocat.json"
    if path.exists():
        try:
            saved = json.loads(path.read_text())
            return saved["username"], saved["password"], saved.get("store", store)
        except (ValueError, KeyError, OSError):
            pass  # unreadable or malformed -> regenerate below

    creds = {"username": "memocat", "password": secrets.token_urlsafe(24), "store": store}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — it is a password on disk
    return creds["username"], creds["password"], creds["store"]


def _publish(host: str, port: int, user: str, password: str, store: str) -> None:
    """Hand the connection to the server module via the env it already reads."""
    os.environ.setdefault("MONTYCAT_HOST", host)
    os.environ.setdefault("MONTYCAT_PORT", str(port))
    os.environ["MONTYCAT_USERNAME"] = user
    os.environ["MONTYCAT_PASSWORD"] = password
    os.environ.setdefault("MONTYCAT_STORE", store)


# ── tier 2: native binary ────────────────────────────────────────────────────

def platform_slug() -> Optional[str]:
    """Artifact platform, or None where no build exists.

    macOS ships one universal binary covering Intel and Apple Silicon, so the
    machine architecture does not appear in the macOS slug.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return "macos-universal"
    if system == "linux" and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    if system == "windows" and machine in ("amd64", "x86_64"):
        return "windows-x86_64"
    return None


def resolve_binary_url(version: str = "latest") -> Optional[str]:
    """Where to fetch the engine archive.

    `MEMOCAT_BINARY_URL` overrides it entirely — that is how this tier is
    developed and tested before any artifact is published, and the escape hatch
    for air-gapped installs.
    """
    override = os.environ.get("MEMOCAT_BINARY_URL")
    if override:
        return override
    slug = platform_slug()
    if slug is None:
        return None
    return f"{DOWNLOAD_BASE}/montycat-semantic_{version}_{slug}.tar.gz"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch(url: str, dest: Path) -> None:
    if url.startswith("file://") or "://" not in url:
        source = url[len("file://"):] if url.startswith("file://") else url
        shutil.copyfile(source, dest)
        return
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed host
        with dest.open("wb") as out:
            shutil.copyfileobj(response, out)


def _unpack(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
        return
    with tarfile.open(archive) as tf:
        # Refuse paths that escape the target; a hostile archive should not be
        # able to write outside the cache directory.
        for member in tf.getmembers():
            resolved = (target / member.name).resolve()
            if not str(resolved).startswith(str(target.resolve())):
                raise BootstrapError(f"archive entry escapes target: {member.name}")
        try:
            # `data` rejects absolute paths, traversal and special files, and is
            # the default from Python 3.14. Passed explicitly so behaviour is
            # identical across versions instead of changing under us.
            tf.extractall(target, filter="data")
        except TypeError:
            tf.extractall(target)  # Python 3.10 has no `filter` parameter


def _find_binary(root: Path) -> Optional[Path]:
    for candidate in ("montycat_bin", "montycat_bin.exe"):
        for found in root.rglob(candidate):
            return found
    return None


async def download_binary(version: str = "latest") -> Optional[Path]:
    """Download, verify and cache the engine binary. None if unavailable.

    A cached copy is reused, so only the first launch pays for this. The
    checksum is mandatory: an unverified binary is not executed.
    """
    url = resolve_binary_url(version)
    if url is None:
        return None

    cache = _home() / "bin" / version
    existing = _find_binary(cache) if cache.exists() else None
    if existing is not None:
        return existing

    def _download() -> Optional[Path]:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            archive = tmpdir / "engine-archive"
            try:
                _fetch(url, archive)
            except (urllib.error.URLError, OSError) as exc:
                logger.debug("native engine archive unavailable at %s: %s", url, exc)
                return None

            try:
                expected = _expected_checksum(url, tmpdir)
            except (urllib.error.URLError, OSError) as exc:
                logger.debug("checksum unavailable for %s: %s", url, exc)
                return None
            if expected is None:
                logger.debug("no checksum published for %s — refusing to run it", url)
                return None
            actual = _sha256(archive)
            if actual != expected:
                raise BootstrapError(
                    f"checksum mismatch for {url}: expected {expected}, got {actual}"
                )

            _unpack(archive, cache)

        binary = _find_binary(cache)
        if binary is None:
            logger.debug("archive contained no montycat_bin")
            return None
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        return binary

    return await asyncio.to_thread(_download)


def _expected_checksum(url: str, tmpdir: Path) -> Optional[str]:
    """The published `.sha256` next to the archive, or None if there isn't one."""
    checksum_file = tmpdir / "checksum"
    _fetch(url + ".sha256", checksum_file)
    text = checksum_file.read_text().strip()
    return text.split()[0] if text else None


async def start_native(host: str, port: int) -> bool:
    binary = await download_binary()
    if binary is None:
        return False

    user, password, store = credentials()
    env = {
        **os.environ,
        "MONTYCAT_SUPEROWNER": user,
        "MONTYCAT_PASSWORD": password,
        "MONTYCAT_SEMANTIC": os.environ.get("MONTYCAT_SEMANTIC", "on"),
    }
    try:
        subprocess.Popen(  # noqa: S603 - our own verified binary
            [str(binary)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # survives this process, by design
        )
    except OSError as exc:
        logger.debug("native engine failed to launch: %s", exc)
        return False

    if not await wait_until_ready(host, port, _timeout()):
        return False
    _publish(host, port, user, password, store)
    return True


# ── tier 3: docker ───────────────────────────────────────────────────────────

def docker_tag() -> str:
    """Architecture-correct image tag.

    On Apple Silicon the plain `semantic` tag is the amd64 image running under
    emulation, where the embedding runtime's warm-up crashes — hence the native
    `arm64-semantic` build.
    """
    machine = platform.machine().lower()
    return "arm64-semantic" if machine in ("arm64", "aarch64") else "semantic"


async def start_docker(host: str, port: int) -> bool:
    if shutil.which("docker") is None:
        return False

    user, password, store = credentials()

    def _run() -> bool:
        existing = subprocess.run(  # noqa: S603
            ["docker", "ps", "-aq", "-f", f"name=^{CONTAINER_NAME}$"],
            capture_output=True, text=True, timeout=30,
        )
        if existing.returncode == 0 and existing.stdout.strip():
            # Reuse the container we started previously — its volume holds the
            # agent's memory, so replacing it would look like data loss.
            return subprocess.run(  # noqa: S603
                ["docker", "start", CONTAINER_NAME],
                capture_output=True, timeout=60,
            ).returncode == 0

        return subprocess.run(  # noqa: S603
            [
                "docker", "run", "-d", "--name", CONTAINER_NAME,
                "-p", f"{port}:21210", "-p", f"{port + 1}:21211",
                "-e", f"MONTYCAT_SUPEROWNER={user}",
                "-e", f"MONTYCAT_PASSWORD={password}",
                "-v", "memocat_data:/var/lib/.montycat",
                f"montygovernance/montycat:{docker_tag()}",
            ],
            capture_output=True, timeout=300,
        ).returncode == 0

    try:
        started = await asyncio.to_thread(_run)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("docker start failed: %s", exc)
        return False
    if not started:
        return False

    if not await wait_until_ready(host, port, _timeout()):
        return False
    _publish(host, port, user, password, store)
    return True


# ── orchestration ────────────────────────────────────────────────────────────

_INSTALL_HELP = (
    "No Montycat engine is running and none could be started automatically.\n"
    "Either:\n"
    "  • start one with Docker — Apple Silicon:\n"
    "      docker run -d -p 21210:21210 -p 21211:21211 \\\n"
    "        -e MONTYCAT_SUPEROWNER=admin -e MONTYCAT_PASSWORD=change-me \\\n"
    "        montygovernance/montycat:arm64-semantic\n"
    "    (x86_64: use the `semantic` tag instead), or\n"
    "  • install it natively — https://montygovernance.com/download\n"
    "Then point MemoCat at it with MONTYCAT_URI, or set MEMOCAT_AUTOSTART=off "
    "to skip this check."
)


async def ensure_engine() -> str:
    """Make an engine available. Returns which tier satisfied the request.

    Raises BootstrapError only when every enabled tier failed — callers should
    surface that message verbatim; it tells the user exactly what to do.
    """
    host, port = _host_port()
    mode = _mode()

    if await asyncio.to_thread(probe, host, port):
        return "existing"

    if mode == "off":
        raise BootstrapError(
            f"No engine at {host}:{port} and MEMOCAT_AUTOSTART=off.\n{_INSTALL_HELP}"
        )

    # An explicit MONTYCAT_URI is a promise that an engine lives there. Starting
    # a different one locally would silently write memories somewhere the user
    # is not looking.
    if os.environ.get("MONTYCAT_URI"):
        raise BootstrapError(
            f"MONTYCAT_URI points at {host}:{port} but nothing is listening there.\n"
            "Start that engine, or unset MONTYCAT_URI to let MemoCat manage one."
        )

    if mode in ("auto", "native") and await start_native(host, port):
        return "native"
    if mode in ("auto", "docker") and await start_docker(host, port):
        return "docker"

    raise BootstrapError(_INSTALL_HELP)
