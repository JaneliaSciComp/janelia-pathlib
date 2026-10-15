"""Share registry: loads share data and provides path translation."""

from __future__ import annotations

import json
import platform
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import TYPE_CHECKING
from urllib.error import URLError

from janelia_pathlib._fetch import default_cache_path, fetch_shares, no_fetch

if TYPE_CHECKING:
    from collections.abc import Sequence

_OS_MAP = {"Darwin": "mac", "Linux": "linux", "Windows": "windows"}
_PATH_KEYS = ("linux_path", "mac_path", "windows_path")
_SMB_MOUNT_LINE = re.compile(r"^//(?:[^@]+@)?([^/]+)/(.+?) on (\S+) \(smbfs")


class Storage(StrEnum):
    """Janelia file share storage types."""

    PRIMARY = "primary"
    HOME = "home"
    SCRATCH = "scratch"
    ARCHIVE = "archive"
    NEARLINE = "nearline"


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


def _path_key(os_name: str) -> str:
    return f"{os_name}_path"


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

    Accepts Linux, Mac, and Windows paths, as well as ``janelia://`` URLs. A
    bare ``/Volumes/<name>`` Mac path is inherently ambiguous when two
    shares collide on the same mount name (macOS names mountpoints after
    the share name alone, ignoring the host) -- in that case this returns
    whichever share happens to match first, same as a plain string guess.
    Use the unambiguous ``janelia://host/share`` form (see
    ``resolve_janelia_url``) or a canonical Linux path to always resolve
    correctly.
    """
    if shares is None:
        shares = _load_shares()

    if path_str.strip().startswith("janelia://"):
        return resolve_janelia_url(path_str, shares)

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


def get_share(group: str, storage: str = "primary") -> Share:
    """Look up a share by group name and storage type.

    Args:
        group: The AD/unix group name (e.g. 'alpha', 'bravo').
        storage: The storage type (e.g. 'primary', 'home', 'scratch', 'archive').
            Defaults to 'primary'.

    Returns:
        The matching Share.

    Raises:
        KeyError: If no share matches the given group and storage.
    """
    for share in _load_shares():
        if share.group == group and share.storage == storage:
            return share
    raise KeyError(f"No share found for group={group!r}, storage={storage!r}")


def translate(path_str: str, target_os: str | None = None) -> str | None:
    """Translate a path from any OS format to the target OS format.

    Args:
        path_str: A file path in any OS format (Linux, Mac, or Windows).
        target_os: Target OS ('linux', 'mac', or 'windows').
            Defaults to the current OS.

    Returns:
        The translated path string, or None if the path doesn't match
        any known share.
    """
    if target_os is None:
        target_os = current_os()

    result = resolve(path_str)
    if result is None:
        return None

    share, subpath = result
    base = getattr(share, _path_key(target_os))
    if not base:
        return None

    if target_os == "mac":
        parts = smb_url_parts(share.mac_smb_url)
        if parts is not None:
            live_base = find_live_mount(*parts)
            if live_base is not None:
                base = live_base

    if not subpath:
        return base

    if target_os == "windows":
        return base + "\\" + subpath.replace("/", "\\")
    return base + "/" + subpath
