"""Share registry: loads share data and provides path translation."""

from __future__ import annotations

import json
import platform
import re
import subprocess
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING
from urllib.error import URLError

from janelia_pathlib._fetch import default_cache_path, fetch_shares, no_fetch

if TYPE_CHECKING:
    from collections.abc import Sequence

_OS_MAP = {"Darwin": "mac", "Linux": "linux", "Windows": "windows"}
_SMB_MOUNT_LINE = re.compile(r"^//(?:[^@]+@)?([^/]+)/(.+?) on (\S+) \(smbfs")


@dataclass(frozen=True, slots=True)
class Share:
    """A single file share entry with per-OS paths."""

    name: str
    group: str
    storage: str
    linux_path: str
    mac_path: str
    windows_path: str
    mac_smb_url: str = ""


def smb_url_parts(smb_url: str) -> tuple[str, str] | None:
    """Split an ``smb://host/share`` URL into ``(host, share_name)``."""
    match = re.match(r"smb://([^/]+)/(.+)", smb_url)
    if match is None:
        return None
    return match.group(1), match.group(2)


def janelia_url(share: Share) -> str | None:
    """The unambiguous ``janelia://host/share_name`` identity for a share.

    Unlike the share's static ``mac_path`` guess, (host, share_name) never
    collides: two shares can both want ``/Volumes/shroff`` on macOS, but
    their SMB host+share coordinates are always distinct.
    """
    parts = smb_url_parts(share.mac_smb_url)
    if parts is None:
        return None
    return f"janelia://{parts[0]}/{parts[1]}"


def _live_smb_mounts() -> list[tuple[str, str, str]]:
    """List live SMB mounts as ``(host, share_name, mountpoint)`` from `mount` output."""
    try:
        output = subprocess.run(
            ["mount"], capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return []

    mounts = []
    for line in output.splitlines():
        match = _SMB_MOUNT_LINE.match(line)
        if match:
            mounts.append((match.group(1), match.group(2), match.group(3)))
    return mounts


def find_live_mount(host: str, share_name: str) -> str | None:
    """Find the real local mountpoint for an SMB share via live `mount` output.

    macOS assigns the mountpoint name (e.g. ``/Volumes/shroff``) based only on
    the share name, so two shares with the same name on different servers
    collide and the second gets suffixed (``/Volumes/shroff-1``). Matching on
    ``(host, share_name)`` against the live mount table is the only reliable
    way to find which one is actually ours.
    """
    for mount_host, mount_share, mountpoint in _live_smb_mounts():
        if mount_host == host and mount_share == share_name:
            return mountpoint
    return None


def find_share_for_mac_mountpoint(
    mountpoint: str, shares: Sequence[Share]
) -> Share | None:
    """Find which of ``shares`` is actually live-mounted at ``mountpoint``.

    Disambiguates a bare ``/Volumes/<name>`` path when multiple shares have
    the same static ``mac_path`` guess (e.g. nrs.hhmi.org/shroff and
    nearline.hhmi.org/shroff both default to ``/Volumes/shroff``, with the
    second one actually landing at ``/Volumes/shroff-1``) by checking which
    share's SMB host+name is actually mounted there right now.
    """
    for mount_host, mount_share, live_point in _live_smb_mounts():
        if live_point != mountpoint:
            continue
        for share in shares:
            if smb_url_parts(share.mac_smb_url) == (mount_host, mount_share):
                return share
    return None


def _live_windows_drives() -> list[tuple[str, str, str]]:
    """List live Windows SMB drive mappings as ``(host, share_name, drive)``.

    Uses PowerShell's ``Get-SmbMapping``, which returns structured,
    non-localized JSON -- unlike the legacy ``net use`` table, whose
    headers, status words, and column layout are locale-dependent and
    fragile to parse.

    NOTE: implemented from documented PowerShell output format; not
    verified against a real Windows machine.
    """
    try:
        output = subprocess.run(
            ["powershell", "-Command", "Get-SmbMapping | ConvertTo-Json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return []

    if not output.strip():
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]

    drives = []
    for entry in data:
        remote = entry.get("RemotePath", "")
        local = entry.get("LocalPath", "")
        match = re.match(r"\\\\([^\\]+)\\(.+)", remote)
        if match and local:
            drives.append((match.group(1), match.group(2), local))
    return drives


def find_share_for_windows_drive(
    drive: str, shares: Sequence[Share]
) -> Share | None:
    """Find which of ``shares`` is actually mapped to Windows drive letter ``drive``.

    NOTE: implemented from documented PowerShell output format; not
    verified against a real Windows machine.
    """
    drive_norm = drive.rstrip("\\").upper()
    for mount_host, mount_share, local in _live_windows_drives():
        if local.rstrip("\\").upper() != drive_norm:
            continue
        for share in shares:
            if smb_url_parts(share.mac_smb_url) == (mount_host, mount_share):
                return share
    return None


_WINDOWS_DRIVE_RE = re.compile(r"^([A-Za-z]:)(\\.*)?$")


def find_share_for_mounted_path(
    path_str: str, os_name: str, shares: Sequence[Share]
) -> tuple[Share, str] | None:
    """Detect whether ``path_str`` falls under a live-mounted Janelia share.

    Dispatches to the live, OS-specific check -- the only reliable way to
    resolve a path whose mountpoint name doesn't uniquely determine the
    share (macOS mount-name collisions, Windows drive-letter mappings).
    Linux mounts are fixed and never ambiguous, so this always returns None
    on Linux; callers fall back to static ``linux_path`` prefix matching.

    Returns (share, subpath), or None if the live check doesn't apply or
    finds nothing (not an error -- caller should fall back to static
    prefix matching against linux_path/mac_path/windows_path).
    """
    normalized = _to_forward_slash(path_str.strip())
    normalized = re.sub(r"/+$", "", normalized)

    if os_name == "mac":
        parts = normalized.split("/")
        # /Volumes/<name>/rest... -> mountpoint is /Volumes/<name>
        if len(parts) >= 3 and parts[1] == "Volumes":
            mountpoint = "/".join(parts[:3])
            share = find_share_for_mac_mountpoint(mountpoint, shares)
            if share is not None:
                subpath = normalized[len(mountpoint) :]
                return share, subpath[1:] if subpath.startswith("/") else subpath
        return None

    if os_name == "windows":
        match = _WINDOWS_DRIVE_RE.match(path_str.strip())
        if match is None:
            return None
        drive, rest = match.group(1), match.group(2) or ""
        share = find_share_for_windows_drive(drive, shares)
        if share is not None:
            subpath = _to_forward_slash(rest)
            return share, subpath[1:] if subpath.startswith("/") else subpath
        return None

    return None


@cache
def _load_shares() -> list[Share]:
    """Load share data from the user cache, fetching it if missing."""
    data_path = default_cache_path()
    if not data_path.exists():
        if no_fetch():
            # Offline/CI opt-out (JANELIA_PATHLIB_NO_FETCH): skip the network fetch
            # and degrade to no known shares, so paths pass through untranslated
            # instead of raising.
            return []
        try:
            fetch_shares(data_path)
        except (URLError, OSError) as e:
            raise RuntimeError(
                f"No share data at {data_path} and auto-fetch failed: {e}. "
                "Run `janelia-paths-fetch` from a machine with access to "
                "the Janelia network to populate the cache, or set "
                "JANELIA_PATHLIB_NO_FETCH=1 to run offline without translation."
            ) from e
    data_text = data_path.read_text()
    raw = json.loads(data_text)
    shares = []
    for entry in raw:
        linux = entry.get("linux_path") or ""
        mac = entry.get("mac_path") or ""
        windows = entry.get("windows_path") or ""
        if not (linux or mac or windows):
            continue
        shares.append(
            Share(
                name=entry.get("zone", ""),
                group=entry.get("group", ""),
                storage=entry.get("storage", ""),
                linux_path=linux,
                mac_path=mac,
                windows_path=windows,
                mac_smb_url=entry.get("mac_smb_url", ""),
            )
        )
    return shares


def _to_forward_slash(path: str) -> str:
    return path.replace("\\", "/")


def current_os() -> str:
    """Return the current OS as 'linux', 'mac', or 'windows'."""
    os_name = _OS_MAP.get(platform.system())
    if os_name is None:
        raise RuntimeError(f"Unsupported platform: {platform.system()}")
    return os_name


def resolve_janelia_url(
    path_str: str, shares: Sequence[Share] | None = None
) -> tuple[Share, str] | None:
    """Match a ``janelia://host/share_name/subpath`` string against known shares.

    Unlike a bare ``/Volumes/<name>`` path, the host in this identity makes
    resolution unambiguous even when two shares have colliding mac_path
    guesses (e.g. ``nrs.hhmi.org/shroff`` vs ``nearline.hhmi.org/shroff``
    both nominally want ``/Volumes/shroff``).
    """
    if shares is None:
        shares = _load_shares()

    match = re.match(r"janelia://([^/]+)/(.+)", path_str)
    if match is None:
        return None
    host, rest = match.group(1), match.group(2).rstrip("/")

    best_share: Share | None = None
    best_share_name = ""

    for share in shares:
        share_parts = smb_url_parts(share.mac_smb_url)
        if share_parts is None or share_parts[0] != host:
            continue
        share_name = share_parts[1]
        if rest != share_name and not rest.startswith(share_name + "/"):
            continue
        if len(share_name) > len(best_share_name):
            best_share = share
            best_share_name = share_name

    if best_share is None:
        return None

    subpath = rest[len(best_share_name) :]
    if subpath.startswith("/"):
        subpath = subpath[1:]
    return best_share, subpath


def resolve(
    path_str: str, shares: Sequence[Share] | None = None
) -> tuple[Share, str] | None:
    """Match a path string against known shares on any OS.

    Returns (share, subpath) where subpath is the relative portion after
    the share mount prefix, or None if no share matches.

    On macOS and Windows, first checks live mount state
    (``find_share_for_mounted_path``) to resolve ambiguous cases correctly:
    a bare ``/Volumes/<name>`` Mac path can't be told apart from a static
    string alone when two shares collide on the same mount name (macOS
    names mountpoints after the share name alone, ignoring the host), and
    a Windows drive letter isn't in the registry at all -- only live state
    can map it to a share. Falls back to static
    linux_path/mac_path/windows_path prefix matching if the live check
    doesn't apply or finds nothing.
    """
    if shares is None:
        shares = _load_shares()

    live_result = find_share_for_mounted_path(path_str, current_os(), shares)
    if live_result is not None:
        return live_result

    normalized = _to_forward_slash(path_str.strip())
    # Strip trailing slash for matching
    normalized = re.sub(r"/+$", "", normalized)

    best_share: Share | None = None
    best_prefix = ""

    for share in shares:
        candidates = [share.linux_path, share.mac_path]
        if share.windows_path:
            candidates.append(_to_forward_slash(share.windows_path))

        for candidate in candidates:
            if not candidate:
                continue
            candidate_norm = re.sub(r"/+$", "", candidate)
            if normalized.startswith(candidate_norm) and len(candidate_norm) > len(
                best_prefix
            ):
                rest = normalized[len(candidate_norm) :]
                if rest == "" or rest.startswith("/"):
                    best_share = share
                    best_prefix = candidate_norm

    if best_share is None:
        return None

    subpath = normalized[len(best_prefix) :]
    if subpath.startswith("/"):
        subpath = subpath[1:]
    return best_share, subpath
