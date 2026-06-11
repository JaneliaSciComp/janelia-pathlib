"""Share registry: loads share data and provides path translation."""

from __future__ import annotations

import json
import platform
import re
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


def resolve(
    path_str: str, shares: Sequence[Share] | None = None
) -> tuple[Share, str] | None:
    """Match a path string against known shares on any OS.

    Returns (share, subpath) where subpath is the relative portion after
    the share mount prefix, or None if no share matches.
    """
    if shares is None:
        shares = _load_shares()

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

    if not subpath:
        return base

    if target_os == "windows":
        return base + "\\" + subpath.replace("/", "\\")
    return base + "/" + subpath
