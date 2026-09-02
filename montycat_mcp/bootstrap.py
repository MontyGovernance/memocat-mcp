"""Zero-config engine bootstrap (AUTOSTART_PLAN.md).

`uvx montycat-mcp` should work on a machine with no engine and no configuration.
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
import ipaddress
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
from typing import NamedTuple, Optional
from urllib.parse import urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 21210
DOWNLOAD_BASE = "https://downloads.montygovernance.com/bin"
CONTAINER_NAME = "montycat-engine"
DEFAULT_STORE = "montycat"
RELEASE_CATALOG_BASE = "https://infra.montygovernance.com"
_BINARY_NAMES = ("montycat_bin", "montycat_bin.exe")
_APT_SEMANTIC_INSTALL = (
    "curl -fsSL https://repo-deb.montygovernance.com/KEY.gpg "
    "| sudo gpg --dearmor -o /usr/share/keyrings/montycat-archive-keyring.gpg "
    "&& echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/montycat-archive-keyring.gpg] "
    "https://repo-deb.montygovernance.com stable main' "
    "| sudo tee /etc/apt/sources.list.d/montycat.list > /dev/null "
    "&& sudo apt update && sudo apt install -y montycat-semantic"
)

logger = __import__("logging").getLogger("montycat.bootstrap")


def _user_agent() -> str:
    from . import __version__

    return f"montycat-mcp/{__version__} (+https://github.com/MontyGovernance/montycat-mcp)"


def _urlopen(url: str, timeout: float):
    """Open a URL with an identifying User-Agent.

    The release catalog and download host sit behind a WAF that answers 403 to
    urllib's default `Python-urllib/x.y` agent. Every other agent — including an
    empty one — is served normally, so the block is on that string specifically.
    Left unset, installer discovery and every artifact download fail closed,
    `start_native` silently returns False, and a machine without Docker cannot
    bootstrap an engine at all.
    """
    request = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310


class BootstrapError(RuntimeError):
    """No engine could be reached or started."""


# ── environment ──────────────────────────────────────────────────────────────

def _env(name: str, default=None):
    """Read canonical configuration, then the deprecated MemoCat spelling."""
    value = os.environ.get(name)
    if value is not None:
        return value
    if name.startswith("MONTYCAT_"):
        legacy = f"MEMOCAT_{name.removeprefix('MONTYCAT_')}"
        value = os.environ.get(legacy)
        if value is not None:
            return value
    return default

def _home() -> Path:
    return Path(_env("MONTYCAT_HOME", Path.home() / ".montycat"))


def _mode() -> str:
    """`auto` | `off` | `native` | `docker`."""
    # A cleared MCPB settings field arrives as an empty string, so fall back
    # after stripping rather than relying on `get`'s absent-variable default.
    return _env("MONTYCAT_AUTOSTART", "").strip().lower() or "auto"


def _timeout() -> int:
    """Readiness budget. A cold semantic start downloads an embedding model
    before it serves, so this is minutes, not seconds."""
    try:
        return int(_env("MONTYCAT_ENGINE_TIMEOUT", "120"))
    except ValueError:
        return 120


def _installer_timeout() -> int:
    try:
        return max(30, int(_env("MONTYCAT_INSTALLER_TIMEOUT", "300")))
    except ValueError:
        return 300


def _host_port() -> tuple[str, int]:
    uri = _env("MONTYCAT_URI")
    if uri:
        try:
            parsed = urlparse(uri)
            if parsed.hostname is not None and parsed.port is not None:
                return parsed.hostname, parsed.port
        except ValueError:
            pass
    return (
        _env("MONTYCAT_HOST", DEFAULT_HOST),
        int(_env("MONTYCAT_PORT", str(DEFAULT_PORT))),
    )


def _is_local(host: str) -> bool:
    """Whether an engine at ``host`` would be one this machine can start.

    The engine is often remote over TCP. Tiers 2 and 3 only ever bind locally,
    so for any other address they would start an engine nobody is watching and
    then wait out the full readiness budget against an address that cannot
    answer.
    """
    candidate = host.strip().strip("[]").lower()
    if candidate in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "::"):
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _probe_timeout(host: str) -> float:
    """Connect budget for the first probe.

    A loopback engine answers immediately or not at all; a remote one crosses a
    network, where 1.5s is tight.
    """
    override = _env("MONTYCAT_PROBE_TIMEOUT")
    if override:
        try:
            return max(0.1, float(override))
        except ValueError:
            pass
    return 1.5 if _is_local(host) else 5.0


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
    env_user = _env("MONTYCAT_USERNAME")
    env_pass = _env("MONTYCAT_PASSWORD")
    store = _env("MONTYCAT_STORE", DEFAULT_STORE)
    if env_user and env_pass:
        return env_user, env_pass, store

    path = _home() / "montycat.json"
    legacy_path = _home() / "memocat.json"
    for candidate in (path, legacy_path):
        if candidate.exists():
            try:
                saved = json.loads(candidate.read_text())
                return saved["username"], saved["password"], saved.get("store", store)
            except (ValueError, KeyError, OSError):
                pass  # unreadable or malformed -> try the next source

    creds = {"username": "montycat", "password": secrets.token_urlsafe(24), "store": store}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — it is a password on disk
    return creds["username"], creds["password"], creds["store"]


def _publish_existing_credentials(host: str, port: int) -> None:
    """Restore credentials for a local engine managed on an earlier launch.

    Merely probing a port cannot identify an arbitrary user's engine, so never
    generate credentials in this path. Publish only explicit environment
    credentials or a credential file that Montycat MCP already created.
    """
    env_user = _env("MONTYCAT_USERNAME")
    env_pass = _env("MONTYCAT_PASSWORD")
    if env_user and env_pass:
        _publish(host, port, env_user, env_pass, _env("MONTYCAT_STORE", DEFAULT_STORE))
        return

    path = _home() / "montycat.json"
    legacy_path = _home() / "memocat.json"
    path = path if path.exists() else legacy_path
    if not path.exists():
        return
    try:
        saved = json.loads(path.read_text())
        _publish(host, port, saved["username"], saved["password"],
                 saved.get("store", _env("MONTYCAT_STORE", DEFAULT_STORE)))
    except (ValueError, KeyError, OSError):
        return


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

    `MONTYCAT_BINARY_URL` overrides it entirely — that is how this tier is
    developed and tested before any artifact is published, and the escape hatch
    for air-gapped installs.
    """
    override = _env("MONTYCAT_BINARY_URL")
    if override:
        return override
    slug = platform_slug()
    if slug is None:
        return None
    return f"{DOWNLOAD_BASE}/montycat-semantic_{version}_{slug}.tar.gz"


def installer_url(version: Optional[str] = None) -> Optional[str]:
    """Explicit installer override for air-gapped or release-pinned installs.

    Normal installs discover the current artifact through the release catalog;
    this direct URL exists only for an operator who deliberately names a
    version or complete URL.
    """
    override = _env("MONTYCAT_INSTALLER_URL")
    if override:
        return override
    version = version or _env("MONTYCAT_ENGINE_VERSION")
    if not version:
        return None
    system = platform.system().lower()
    if system == "darwin":
        if platform.machine().lower() not in ("arm64", "aarch64"):
            return None
        return f"https://downloads.montygovernance.com/macos/montycat-semantic_{version}_arm64.pkg"
    if system == "windows" and platform.machine().lower() in ("amd64", "x86_64"):
        return f"https://downloads.montygovernance.com/windows/montycat-semantic_{version}.msi"
    return None


def _discover_latest_installer_url() -> Optional[str]:
    """Return the catalog-selected Semantic installer for this platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in ("arm64", "aarch64"):
        catalog_platform, required_arch = "darwin", "arm64"
    elif system == "windows" and machine in ("amd64", "x86_64"):
        catalog_platform, required_arch = "windows", None
    else:
        return None
    base = _env("MONTYCAT_RELEASES_URL", RELEASE_CATALOG_BASE).rstrip("/")
    try:
        with _urlopen(f"{base}/v1/releases/{catalog_platform}", timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    editions = payload.get("editions") if isinstance(payload, dict) else None
    if not isinstance(editions, list):
        return None
    for release in editions:
        if not isinstance(release, dict) or release.get("edition") != "semantic":
            continue
        if required_arch and release.get("arch") != required_arch:
            continue
        url = release.get("url")
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            return url
    return None


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
    with _urlopen(url, timeout=60) as response:
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
    explicit = _env("MONTYCAT_ENGINE_BINARY")
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


def find_installed_cli(binary: Optional[Path] = None) -> Optional[Path]:
    """Locate the `montycat` CLI that ships beside the engine.

    Every packaging path installs the two together — the macOS pkg and the
    Docker image into `/usr/local/bin`, the Debian package into `/usr/bin` —
    so look next to a known engine binary first and fall back to PATH.
    """
    explicit = _env("MONTYCAT_ENGINE_CLI")
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    names = ("montycat.exe",) if os.name == "nt" else ("montycat",)
    if binary is not None:
        for name in names:
            beside = binary.parent / name
            if beside.is_file():
                return beside
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


class CliProbe(NamedTuple):
    """What `montycat version` told us.

    `ran` and the version are deliberately separate. Whether the process
    executed is a fact about the installation; whether we recognised its output
    is a fact about this parser. A future CLI that reworded its version line
    still proves the binary runs, and must not be mistaken for a bad install.
    """

    ran: bool
    edition: Optional[str] = None
    version: Optional[str] = None


def probe_cli(cli: Optional[Path] = None) -> CliProbe:
    """Run `montycat version`.

    The CLI prints a compile-time constant, so this needs no running engine —
    which is the whole point: it answers exactly when the engine is *down* and
    we are deciding whether a local install is usable. Semantic builds print
    `Montycat Semantic <version>`, the lean edition `Montycat <version>`, and
    both go to stderr with stdout left empty.
    """
    cli = cli or find_installed_cli()
    if cli is None:
        return CliProbe(ran=False)
    env = {**os.environ, **_library_path(cli.parent)}
    try:
        completed = subprocess.run(  # noqa: S603 - our own verified binary
            [str(cli), "version"],
            capture_output=True, text=True, timeout=10, env=env,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        # Wrong architecture, unresolvable ONNX libraries, no execute bit, a
        # truncated install, or a hang.
        logger.debug("montycat CLI did not run: %s", exc)
        return CliProbe(ran=False)
    if completed.returncode != 0:
        logger.debug("montycat version exited %s", completed.returncode)
        return CliProbe(ran=False)

    match = re.search(
        r"^Montycat(?P<edition> Semantic)? (?P<version>\S+)\s*$",
        f"{completed.stderr or ''}\n{completed.stdout or ''}",
        re.MULTILINE,
    )
    if match is None:
        # It ran, so the install is sound; we simply do not recognise this
        # wording. Report the version as unknown rather than condemning it.
        logger.debug("unrecognised montycat version output: %r", completed.stderr)
        return CliProbe(ran=True)
    return CliProbe(
        ran=True,
        edition="semantic" if match.group("edition") else "base",
        version=match.group("version"),
    )


def engine_build(cli: Optional[Path] = None) -> Optional[tuple[str, str]]:
    """`(edition, version)`, or None when that could not be determined."""
    probe = probe_cli(cli)
    if probe.edition is None or probe.version is None:
        return None
    return (probe.edition, probe.version)


def _cache_valid(cache: Path) -> Optional[Path]:
    """Return a cached binary only when its recorded hash still matches."""
    binary = _find_binary(cache) if cache.exists() else None
    digest_file = cache / ".montycat-binary.sha256"
    if not digest_file.is_file():
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
                (staged / ".montycat-binary.sha256").write_text(_sha256(binary))

                if cache.exists():
                    shutil.rmtree(cache)
                os.rename(staged, cache)
                return _cache_valid(cache)

    return await asyncio.to_thread(_download)


async def download_installer(version: Optional[str] = None) -> Optional[Path]:
    """Download a verified macOS/Windows installer into the user cache."""
    pinned = version or _env("MONTYCAT_ENGINE_VERSION")
    override = _env("MONTYCAT_INSTALLER_URL")
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
    command = _env("MONTYCAT_APT_INSTALL_COMMAND")
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
    """Launch an engine that is *already* installed.

    Deliberately never installs. Acquiring the engine opens an OS installer and
    asks for administrator consent (or, on Linux, runs `sudo apt install`), and
    that cannot happen as an invisible side effect of a user opening a chat
    client — see `install_engine`.
    """
    binary = find_installed_binary()
    if binary is None:
        return False

    # A file on disk is not proof it runs — wrong architecture, or the ONNX
    # libraries `_library_path` exists to locate never resolve. The CLI ships
    # beside the engine in every packaging, so when it is present but cannot
    # answer, the engine next to it will not start either. Say so now instead
    # of launching into a DEVNULL and waiting out the whole readiness budget.
    cli = find_installed_cli(binary)
    if cli is not None:
        probe = probe_cli(cli)
        if not probe.ran:
            logger.warning(
                "montycat is installed at %s but does not run; skipping the "
                "native engine", binary.parent,
            )
            return False
        if probe.version:
            logger.info("found Montycat %s (%s edition)", probe.version, probe.edition)

    user, password, store = credentials()
    env = {
        **os.environ,
        "MONTYCAT_SUPEROWNER": user,
        "MONTYCAT_PASSWORD": password,
        "MONTYCAT_SEMANTIC": _env("MONTYCAT_SEMANTIC", "on"),
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
                "-v", "montycat_data:/var/lib/.montycat",
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
    "  • or ask me to run `montycat_install_engine`, which downloads the package "
    "and opens your operating system's installer. It will ask for your "
    "administrator password.\n"
    "Then point Montycat MCP at it with MONTYCAT_URI, or set MONTYCAT_AUTOSTART=off "
    "to skip this check."
)


def _remote_engine_help(host: str, port: int) -> str:
    return (
        f"No Montycat engine is answering at {host}:{port}.\n"
        "That address is not on this machine, so Montycat MCP will not start an "
        "engine for it — doing so would create a second, local database and "
        "write memories somewhere you are not looking.\n"
        "Start the engine on that host, correct MONTYCAT_URI / MONTYCAT_HOST, "
        "or unset them to let Montycat MCP manage a local engine."
    )


async def ensure_engine() -> str:
    """Make an engine available. Returns which tier satisfied the request.

    Raises BootstrapError only when every enabled tier failed — callers should
    surface that message verbatim; it tells the user exactly what to do.
    """
    host, port = _host_port()
    mode = _mode()

    if await asyncio.to_thread(probe, host, port, _probe_timeout(host)):
        if not _env("MONTYCAT_URI"):
            _publish_existing_credentials(host, port)
        return "existing"

    if mode == "off":
        # _INSTALL_HELP closes by offering MONTYCAT_AUTOSTART=off, which is the
        # setting that produced this branch. Say what actually helps here.
        raise BootstrapError(
            f"No engine at {host}:{port} and MONTYCAT_AUTOSTART=off.\n"
            f"{_INSTALL_HELP.rsplit('Then point Montycat MCP at it', 1)[0]}"
            "Then point Montycat MCP at it with MONTYCAT_URI, or set "
            "MONTYCAT_AUTOSTART=auto to let Montycat MCP start one."
        )

    # An explicit MONTYCAT_URI is a promise that an engine lives there. Starting
    # a different one locally would silently write memories somewhere the user
    # is not looking.
    if _env("MONTYCAT_URI"):
        raise BootstrapError(
            f"MONTYCAT_URI points at {host}:{port} but nothing is listening there.\n"
            "Start that engine, or unset MONTYCAT_URI to let Montycat MCP manage one."
        )

    # Same reasoning without a URI: MONTYCAT_HOST can name another machine, and
    # tiers 2 and 3 only ever bind locally. Left unchecked they would start an
    # engine nobody is watching, then wait out the full readiness budget against
    # an address that cannot answer — twice, leaving a stray container behind.
    if not _is_local(host):
        raise BootstrapError(_remote_engine_help(host, port))

    if mode in ("auto", "native") and await start_native(host, port):
        return "native"
    if mode in ("auto", "docker") and await start_docker(host, port):
        return "docker"

    raise BootstrapError(_INSTALL_HELP)


async def install_engine() -> str:
    """Acquire a native engine, with the user's knowledge, then start it.

    Kept out of `ensure_engine` on purpose. This opens the operating system's
    installer — which asks for an administrator password — or on Linux runs the
    documented `sudo apt install`. Neither belongs in the invisible startup path
    of a chat client; both are fine when the user has just asked for them.

    Returns a sentence describing what happened, for the calling tool to relay.
    """
    host, port = _host_port()

    if _env("MONTYCAT_URI"):
        raise BootstrapError(
            "MONTYCAT_URI is set, so Montycat MCP is configured to use the engine at "
            f"{host}:{port}. Installing a local engine would create a second "
            "database and write memories somewhere you are not looking.\n"
            "Unset MONTYCAT_URI first if you want Montycat MCP to manage its own "
            "engine, or start the configured one."
        )
    if not _is_local(host):
        raise BootstrapError(_remote_engine_help(host, port))

    if await asyncio.to_thread(probe, host, port, _probe_timeout(host)):
        _publish_existing_credentials(host, port)
        build = await asyncio.to_thread(engine_build)
        running = f"Montycat {build[1]} ({build[0]} edition)" if build else "An engine"
        return f"{running} is already running at {host}:{port}; nothing to install."

    if find_installed_binary() is None:
        system = platform.system().lower()
        if system in ("darwin", "windows"):
            installed = await install_desktop_package()
        elif system == "linux":
            installed = await install_linux_apt()
        else:
            raise BootstrapError(
                f"No Montycat installer is published for {system}. "
                f"Use Docker instead:\n{_INSTALL_HELP}"
            )
        if not installed:
            raise BootstrapError(
                "The Montycat installer did not complete. If the installer "
                "window is still open, finish it and ask me again.\n"
                f"{_INSTALL_HELP}"
            )

    if not await start_native(host, port):
        raise BootstrapError(
            "Montycat installed but the engine did not become reachable at "
            f"{host}:{port}. Check that nothing else is using that port.\n"
            f"{_INSTALL_HELP}"
        )

    build = await asyncio.to_thread(engine_build)
    if build is None:
        return f"Montycat engine installed and running at {host}:{port}."
    edition, version = build
    detail = f"Montycat {version} ({edition} edition) is running at {host}:{port}."
    if edition != "semantic":
        # Every semantic tool needs the Semantic edition. Better one clear
        # sentence now than a confusing failure per tool later.
        detail += (
            " Semantic search, vector RAG, and embedding tools need the "
            "Semantic edition — reinstall it from "
            "https://montygovernance.com/download to use them."
        )
    return detail
