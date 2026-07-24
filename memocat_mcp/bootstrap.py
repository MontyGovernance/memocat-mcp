"""Zero-config engine bootstrap (AUTOSTART_PLAN.md).

`uvx memocat-mcp` should work on a machine with no engine and no configuration.
Four tiers, tried in order, first success wins:

  1. an engine is already reachable (or MONTYCAT_URI is set) -> just use it
  2. a detected/installed native engine; if absent, invoke the official
     platform installer (macOS/Windows) or APT (Linux)
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
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.parse import urljoin

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 21210
DOWNLOAD_BASE = "https://downloads.montygovernance.com/bin"
CONTAINER_NAME = "memocat-engine"
DEFAULT_STORE = "memocat"
MACOS_INSTALLER_BASE = "https://downloads.montygovernance.com/macos"
WINDOWS_INSTALLER_BASE = "https://downloads.montygovernance.com/windows"
DEFAULT_ENGINE_VERSION = "1.2.3"
_BINARY_NAMES = ("montycat_bin", "montycat_bin.exe")
_APT_SEMANTIC_INSTALL = (
    "curl -fsSL https://repo-deb.montygovernance.com/KEY.gpg "
    "| sudo gpg --dearmor -o /usr/share/keyrings/montycat-archive-keyring.gpg "
    "&& echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/montycat-archive-keyring.gpg] "
    "https://repo-deb.montygovernance.com stable main' "
    "| sudo tee /etc/apt/sources.list.d/montycat.list > /dev/null "
    "&& sudo apt update && sudo apt install -y montycat-semantic"
)

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


def _installer_timeout() -> int:
    try:
        return max(30, int(os.environ.get("MEMOCAT_INSTALLER_TIMEOUT", "300")))
    except ValueError:
        return 300


def _host_port() -> tuple[str, int]:
    uri = os.environ.get("MONTYCAT_URI")
    if uri:
        try:
            parsed = urlparse(uri)
            if parsed.hostname is not None and parsed.port is not None:
                return parsed.hostname, parsed.port
        except ValueError:
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


def installer_url(version: Optional[str] = None) -> Optional[str]:
    """Pinned/fallback Semantic installer URL for the current platform."""
    override = os.environ.get("MEMOCAT_INSTALLER_URL")
    if override:
        return override
    version = version or os.environ.get("MEMOCAT_ENGINE_VERSION", DEFAULT_ENGINE_VERSION)
    system = platform.system().lower()
    if system == "darwin":
        if platform.machine().lower() not in ("arm64", "aarch64"):
            return None
        return f"{MACOS_INSTALLER_BASE}/montycat-semantic_{version}_arm64.pkg"
    if system == "windows" and platform.machine().lower() in ("amd64", "x86_64"):
        return f"{WINDOWS_INSTALLER_BASE}/montycat-semantic_{version}.msi"
    return None


def _discover_latest_installer_url() -> Optional[str]:
    """Choose the highest stable Semantic package in the platform index.

    Download directories are the release source of truth. Version comparison
    is numeric, so 1.10.0 correctly sorts after 1.9.9. If an index is
    unavailable, callers fall back to DEFAULT_ENGINE_VERSION.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in ("arm64", "aarch64"):
        base = MACOS_INSTALLER_BASE + "/"
        pattern = re.compile(
            r"(montycat-semantic_(\d+)\.(\d+)\.(\d+)_arm64\.pkg)"
        )
    elif system == "windows" and machine in ("amd64", "x86_64"):
        base = WINDOWS_INSTALLER_BASE + "/"
        pattern = re.compile(
            r"(montycat-semantic_(\d+)\.(\d+)\.(\d+)\.msi)"
        )
    else:
        return None
    try:
        with urllib.request.urlopen(base, timeout=15) as response:  # noqa: S310
            listing = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError):
        return None
    candidates = {
        (int(major), int(minor), int(patch), filename)
        for filename, major, minor, patch in pattern.findall(listing)
    }
    if not candidates:
        return None
    filename = max(candidates)[3]
    return urljoin(base, filename)


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


def _inside(target: Path, member: str) -> bool:
    """Whether an archive member resolves inside ``target``."""
    try:
        (target / member).resolve().relative_to(target.resolve())
        return True
    except ValueError:
        return False


def _unpack(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                # Zip does not provide tar's ``data`` filter. Reject links as
                # well as traversal/absolute names rather than trusting the
                # extractor's version-specific path cleanup.
                mode = member.external_attr >> 16
                if not _inside(target, member.filename) or stat.S_ISLNK(mode):
                    raise BootstrapError(f"archive entry escapes target: {member.filename}")
            zf.extractall(target)
        return
    with tarfile.open(archive) as tf:
        # Refuse paths that escape the target; a hostile archive should not be
        # able to write outside the cache directory.
        for member in tf.getmembers():
            if not _inside(target, member.name):
                raise BootstrapError(f"archive entry escapes target: {member.name}")
        try:
            # `data` rejects absolute paths, traversal and special files, and is
            # the default from Python 3.14. Passed explicitly so behaviour is
            # identical across versions instead of changing under us.
            tf.extractall(target, filter="data")
        except TypeError:
            tf.extractall(target)  # Python 3.10 has no `filter` parameter


def _find_binary(root: Path) -> Optional[Path]:
    for candidate in _BINARY_NAMES:
        for found in root.rglob(candidate):
            return found
    return None


def find_installed_binary() -> Optional[Path]:
    """Find an engine installed by the user or a platform installer.

    An explicit location wins; PATH covers normal Linux installs. The extra
    locations cover the conventional macOS and Windows installer destinations,
    whose PATH updates are not visible to the already-running MCP process.
    """
    explicit = os.environ.get("MEMOCAT_ENGINE_BINARY")
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    for name in _BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    candidates = [
        Path("/usr/local/bin/montycat_bin"),
        Path("/opt/homebrew/bin/montycat_bin"),
    ]
    if os.name == "nt":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.append(Path(root) / "Montycat" / "montycat_bin.exe")
    return next((path for path in candidates if path.is_file()), None)


def _cache_valid(cache: Path) -> Optional[Path]:
    """Return a cached binary only when its recorded hash still matches."""
    binary = _find_binary(cache) if cache.exists() else None
    digest_file = cache / ".memocat-binary.sha256"
    if binary is None or not digest_file.is_file():
        return None
    try:
        expected = digest_file.read_text().strip()
    except OSError:
        return None
    return binary if expected and _sha256(binary) == expected else None


@contextmanager
def _cache_lock(cache: Path):
    """A small cross-process lock for one cache version.

    Atomic directory creation works on all supported platforms. A stale lock is
    reclaimed after the download timeout plus a small margin, so a killed
    launcher cannot block future starts forever.
    """
    lock = cache.with_name(f"{cache.name}.lock")
    deadline = time.monotonic() + 180
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 300:
                    shutil.rmtree(lock)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise BootstrapError(f"timed out waiting for engine cache lock: {lock}")
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def _library_path(directory: Path) -> dict[str, str]:
    """Make bundled shared libraries discoverable by a native artifact."""
    if os.name == "nt":
        variable = "PATH"
    elif platform.system().lower() == "darwin":
        variable = "DYLD_FALLBACK_LIBRARY_PATH"
    else:
        variable = "LD_LIBRARY_PATH"
    current = os.environ.get(variable, "")
    value = str(directory) if not current else f"{directory}{os.pathsep}{current}"
    return {variable: value}


async def download_binary(version: str = "latest") -> Optional[Path]:
    """Download, verify and cache the engine binary. None if unavailable.

    A cached copy is reused, so only the first launch pays for this. The
    checksum is mandatory: an unverified binary is not executed.
    """
    url = resolve_binary_url(version)
    if url is None:
        return None

    cache = _home() / "bin" / version
    existing = _cache_valid(cache)
    if existing is not None:
        return existing

    def _download() -> Optional[Path]:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with _cache_lock(cache):
            existing = _cache_valid(cache)
            if existing is not None:
                return existing
            with tempfile.TemporaryDirectory(dir=cache.parent) as tmp:
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

                staged = tmpdir / "cache"
                _unpack(archive, staged)
                binary = _find_binary(staged)
                if binary is None:
                    logger.debug("archive contained no montycat_bin")
                    return None
                binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
                (staged / ".memocat-binary.sha256").write_text(_sha256(binary))

                if cache.exists():
                    shutil.rmtree(cache)
                os.rename(staged, cache)
                return _cache_valid(cache)

    return await asyncio.to_thread(_download)


async def download_installer(version: Optional[str] = None) -> Optional[Path]:
    """Download a verified macOS/Windows installer into the user cache."""
    pinned = version or os.environ.get("MEMOCAT_ENGINE_VERSION")
    override = os.environ.get("MEMOCAT_INSTALLER_URL")
    if override or pinned:
        url = installer_url(pinned)
    else:
        url = await asyncio.to_thread(_discover_latest_installer_url)
    if url is None:
        return None

    def _download() -> Optional[Path]:
        suffix = ".pkg" if url.endswith(".pkg") else ".msi"
        filename = Path(urlparse(url).path).name
        cache = _home() / "installers" / (filename or f"montycat-semantic{suffix}")
        digest_file = cache.with_suffix(cache.suffix + ".sha256")
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.is_file() and digest_file.is_file():
            try:
                if _sha256(cache) == digest_file.read_text().strip():
                    return cache
            except OSError:
                pass
        with tempfile.TemporaryDirectory(dir=cache.parent) as tmp:
            tmpdir = Path(tmp)
            staged = tmpdir / f"installer{suffix}"
            try:
                _fetch(url, staged)
                expected = _expected_checksum(url, tmpdir)
            except (urllib.error.URLError, OSError) as exc:
                logger.debug("installer unavailable at %s: %s", url, exc)
                return None
            if not expected:
                logger.debug("no checksum published for installer %s", url)
                return None
            actual = _sha256(staged)
            if actual != expected:
                raise BootstrapError(
                    f"checksum mismatch for {url}: expected {expected}, got {actual}"
                )
            os.replace(staged, cache)
            digest_file.write_text(actual)
        return cache

    return await asyncio.to_thread(_download)


async def install_desktop_package() -> bool:
    """Open the platform installer, allowing macOS/UAC to request consent."""
    package = await download_installer()
    if package is None:
        return False
    system = platform.system().lower()
    if system == "darwin":
        command = ["open", str(package)]
    elif system == "windows":
        command = ["msiexec.exe", "/i", str(package)]
    else:
        return False
    try:
        launched = await asyncio.to_thread(
            lambda: subprocess.run(command, capture_output=True, timeout=30).returncode == 0
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("desktop installer did not start: %s", exc)
        return False
    if not launched:
        return False

    # `open package.pkg` returns when Installer launches, not when the package
    # has finished. Wait for the installed binary before falling through to
    # Docker or trying to launch a path that does not exist yet.
    end = asyncio.get_running_loop().time() + _installer_timeout()
    while asyncio.get_running_loop().time() < end:
        if find_installed_binary() is not None:
            return True
        await asyncio.sleep(1)
    return False


async def install_linux_apt() -> bool:
    """Run Montycat's documented one-command semantic APT installation.

    The official repository is AMD64-only; ARM Linux deliberately skips this
    tier and goes straight to the architecture-correct Docker image.
    """
    if (
        platform.system().lower() != "linux"
        or platform.machine().lower() not in ("x86_64", "amd64")
        or shutil.which("apt-get") is None
    ):
        return False
    command = os.environ.get("MEMOCAT_APT_INSTALL_COMMAND")
    # The documented command needs pipes and redirection. An override is
    # intentionally executed the same way: it is an operator-controlled escape
    # hatch for mirrors and managed package sources.
    args = ["sh", "-c", command or _APT_SEMANTIC_INSTALL]
    try:
        return await asyncio.to_thread(
            lambda: subprocess.run(args, capture_output=True, timeout=120).returncode == 0
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("APT installation did not complete: %s", exc)
        return False


def _expected_checksum(url: str, tmpdir: Path) -> Optional[str]:
    """The published `.sha256` next to the archive, or None if there isn't one."""
    checksum_file = tmpdir / "checksum"
    _fetch(url + ".sha256", checksum_file)
    text = checksum_file.read_text().strip()
    return text.split()[0] if text else None


async def start_native(host: str, port: int) -> bool:
    binary = find_installed_binary()
    if binary is None:
        system = platform.system().lower()
        installed = (
            await install_desktop_package() if system in ("darwin", "windows")
            else await install_linux_apt() if system == "linux" else False
        )
        if not installed:
            return False
        binary = find_installed_binary()
    if binary is None:
        return False

    user, password, store = credentials()
    env = {
        **os.environ,
        "MONTYCAT_SUPEROWNER": user,
        "MONTYCAT_PASSWORD": password,
        "MONTYCAT_SEMANTIC": os.environ.get("MONTYCAT_SEMANTIC", "on"),
        **_library_path(binary.parent),
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
            mapped = subprocess.run(  # noqa: S603
                ["docker", "port", CONTAINER_NAME, "21210/tcp"],
                capture_output=True, text=True, timeout=30,
            )
            ports = [line.rsplit(":", 1)[-1].strip() for line in mapped.stdout.splitlines()]
            if mapped.returncode != 0 or str(port) not in ports:
                logger.warning(
                    "existing %s does not publish engine port %s; refusing to reuse it",
                    CONTAINER_NAME, port,
                )
                return False
            running = subprocess.run(  # noqa: S603
                ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
                capture_output=True, text=True, timeout=30,
            )
            if running.returncode == 0 and running.stdout.strip() == "true":
                return True
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
    "  • install it natively — Linux: configure the Montycat APT repository and "
    "install `montycat`; macOS/Windows: use the package from "
    "https://montygovernance.com/download\n"
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
