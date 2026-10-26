"""Share registry: loads share data and provides path translation."""

from __future__ import annotations

import json
import platform
import re
import warnings
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING
from urllib.error import URLError

from . import _macos, _windows
from ._fetch import fetch_shares, no_fetch, resolve_cache_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_OS_MAP = {"Darwin": "mac", "Linux": "linux", "Windows": "windows"}

_SMB_URL_RE = re.compile(r"smb://([^/]+)/(.+)")


def parse_smb_url(url: str) -> tuple[str, str] | None:
    """Split an ``smb://host/share/...`` URL into ``(host, rest)``.

    Returns None if ``url`` isn't a well-formed ``smb://`` URL.
    """
    match = _SMB_URL_RE.match(url.strip().rstrip("/"))
    if match is None:
        return None
    return match.group(1), match.group(2)


@dataclass(frozen=True, slots=True)
class Share:
    """A single file share entry, matching the Fileglancer API's own field names.

    ``host`` and ``share_name`` are parsed from ``mac_path`` once at load
    (see ``from_api_entry``) rather than on each lookup. Live mount tables
    on macOS and Windows identify a share by that pair.
    """

    zone: str
    linux_path: str
    windows_path: str
    mac_path: str
    host: str
    share_name: str

    @classmethod
    def from_api_entry(cls, entry: Mapping[str, str]) -> Share:
        """Build a Share from one Fileglancer API record.

        Raises:
            ValueError: if ``mac_path`` isn't a well-formed ``smb://`` URL.
        """
        mac_path = entry["mac_path"]
        parsed = parse_smb_url(mac_path)
        if parsed is None:
            raise ValueError(f"Malformed smb path for mac mounting: {mac_path}")
        host, share_name = parsed
        return cls(
            zone=entry["zone"],
            linux_path=entry["linux_path"],
            windows_path=entry["windows_path"],
            mac_path=mac_path,
            host=host,
            share_name=share_name,
        )

    @property
    def uri(self) -> str:
        """The share's canonical ``janelia:/<linux_path>`` identity."""
        return f"janelia:{self.linux_path}"


def find_share_by_host_and_name(
    host: str, share_name: str, shares: Iterable[Share]
) -> Share | None:
    """Find the share served as ``share_name`` by ``host``.

    This is how live mount tables identify a share on macOS and Windows.
    """
    for share in shares:
        if (share.host, share.share_name) == (host, share_name):
            return share
    return None


def _find_share_by_fixed_path(
    path_str: str, shares: Iterable[Share], attr: str
) -> tuple[Share, str] | None:
    """Find which of ``shares`` a path falls under by prefix-matching ``attr``.

    For path fields that are already unambiguous on their own (``linux_path``,
    fixed and admin-assigned; ``windows_path``, a host-qualified UNC path) --
    unlike a macOS mountpoint name or Windows drive letter, neither needs a
    live-state check.
    """
    normalized = _to_forward_slash(path_str.strip())
    normalized = re.sub(r"/+$", "", normalized)

    best_share: Share | None = None
    best_prefix = ""
    for share in shares:
        candidate = _to_forward_slash(re.sub(r"[/\\]+$", "", getattr(share, attr) or ""))
        if not candidate:
            continue
        if normalized.startswith(candidate) and len(candidate) > len(best_prefix):
            rest = normalized[len(candidate) :]
            if rest == "" or rest.startswith("/"):
                best_share = share
                best_prefix = candidate

    if best_share is None:
        return None
    subpath = normalized[len(best_prefix) :]
    return best_share, subpath[1:] if subpath.startswith("/") else subpath


def find_share_for_linux_mount(path_str: str, shares: Iterable[Share]) -> tuple[Share, str] | None:
    """Find which of ``shares`` a Linux path falls under.

    Linux mount points are fixed and admin-assigned, so (unlike macOS
    mount-name collisions or Windows drive letters) a static ``linux_path``
    prefix match is already fully authoritative -- no live state to check.
    """
    return _find_share_by_fixed_path(path_str, shares, "linux_path")


def find_share_for_windows_unc(path_str: str, shares: Iterable[Share]) -> tuple[Share, str] | None:
    """Find which of ``shares`` a Windows UNC path (\\\\host\\share\\...) falls under.

    A UNC path already encodes the host, so (unlike a drive letter) it's
    unambiguous on its own -- no live-state check needed.
    """
    return _find_share_by_fixed_path(path_str, shares, "windows_path")


def find_share_for_smb_url(path_str: str, shares: Iterable[Share]) -> tuple[Share, str] | None:
    """Find which of ``shares`` an ``smb://host/share/...`` URL refers to.

    The host and share name are both in the string, so this is an exact
    lookup -- no live-state check needed.
    """
    parsed = parse_smb_url(path_str)
    if parsed is None:
        return None
    host, rest = parsed

    share_name, _, subpath = rest.partition("/")
    share = find_share_by_host_and_name(host, share_name, shares)
    if share is None:
        return None
    return share, subpath


def find_share_for_mounted_path(
    path_str: str, os_name: str, shares: Iterable[Share]
) -> tuple[Share, str] | None:
    """Detect whether ``path_str`` falls under a mounted Janelia share.

    Dispatches to the OS-specific check: a live mount-table/SMB-mapping
    lookup on macOS and Windows (the only reliable way to resolve a path
    whose mountpoint name doesn't uniquely determine the share -- macOS
    mount-name collisions, Windows drive-letter mappings), or a plain
    static ``linux_path`` prefix match on Linux (already fully
    authoritative there, no live state needed).

    Returns (share, subpath), or None if nothing matches.
    """
    if os_name == "linux":
        return find_share_for_linux_mount(path_str, shares)

    normalized = _to_forward_slash(path_str.strip())
    normalized = re.sub(r"/+$", "", normalized)

    if os_name == "mac":
        parts = normalized.split("/")
        # /Volumes/<name>/rest... -> mountpoint is /Volumes/<name>
        if len(parts) >= 3 and parts[1] == "Volumes":
            mountpoint = "/".join(parts[:3])
            share = _macos.find_share_for_mac_mountpoint(mountpoint, shares)
            if share is not None:
                subpath = normalized[len(mountpoint) :]
                return share, subpath[1:] if subpath.startswith("/") else subpath
        return None

    if os_name == "windows":
        match = _windows.DRIVE_RE.match(path_str.strip())
        if match is not None:
            drive, rest = match.group(1), match.group(2) or ""
            share = _windows.find_share_for_windows_drive(drive, shares)
            if share is not None:
                subpath = _to_forward_slash(rest)
                return share, subpath[1:] if subpath.startswith("/") else subpath
            return None

        # Not a drive letter -- a UNC path (\\host\share\...) is already
        # unambiguous, same as linux_path, so no live-state check is needed.
        return find_share_for_windows_unc(path_str, shares)

    return None


@cache
def _load_shares() -> tuple[Share, ...]:
    """Load share data, refreshing it from the network on every call unless
    JANELIA_PATHLIB_NO_FETCH is set.

    JANELIA_PATHLIB_NO_FETCH means "never touch the network": use the cache
    file as-is if present (however stale). Without it, a fetch failure
    (offline, DNS, etc.) falls back to the existing cache file instead.
    Either way, if there's no cache file to fall back to, this degrades to
    no known shares (with a warning) rather than raising -- paths just pass
    through untranslated instead of breaking the caller.
    """
    cache_file = resolve_cache_path()

    if not no_fetch():
        try:
            fetch_shares(cache_file)
        except (URLError, OSError) as e:
            if cache_file.is_file():
                warnings.warn(
                    f"Failed to refresh share data ({e}); using cached data "
                    f"at {cache_file}, which may be stale.",
                    stacklevel=2,
                )

    if not cache_file.is_file():
        warnings.warn(
            f"No share data available at {cache_file}; paths will not be "
            "translated. Run `janelia-paths-fetch` from a machine with "
            "access to the Janelia network to populate the cache.",
            stacklevel=2,
        )
        return ()

    data_text = cache_file.read_text()
    raw = json.loads(data_text)
    return tuple(Share.from_api_entry(entry) for entry in raw)


def _to_forward_slash(path: str) -> str:
    return path.replace("\\", "/")


def current_os() -> str:
    """Return the current OS as 'linux', 'mac', or 'windows'."""
    os_name = _OS_MAP.get(platform.system())
    if os_name is None:
        raise RuntimeError(f"Unsupported platform: {platform.system()}")
    return os_name


def resolve_janelia_uri(
    path_str: str, shares: Iterable[Share] | None = None
) -> tuple[Share, str] | None:
    """Match a ``janelia:/<linux_path>`` string against known shares.

    The URI body is a Linux path, so this is the same prefix match
    ``find_share_for_linux_mount`` does. A ``janelia://...`` string
    (an older, host-qualified form) is rejected: ``//`` would make the
    first path segment parse as a URI authority.
    """
    if shares is None:
        shares = _load_shares()

    match = re.match(r"janelia:(/(?!/).*)$", path_str.strip())
    if match is None:
        return None
    return find_share_for_linux_mount(match.group(1), shares)


def resolve(path_str: str, shares: Iterable[Share] | None = None) -> tuple[Share, str] | None:
    """Match a path string against known shares on any OS.

    Returns (share, subpath) where subpath is the relative portion after
    the share mount prefix, or None if no share matches.

    First checks whether the path falls under a mounted share for the
    current OS (``find_share_for_mounted_path``) -- on macOS and Windows
    this consults live mount state to resolve cases a static string alone
    can't: a bare ``/Volumes/<name>`` Mac path can't be told apart from a
    static string alone when two shares collide on the same mount name
    (macOS names mountpoints after the share name alone, ignoring the
    host), and a Windows drive letter isn't in the registry at all -- only
    live state can map it to a share. On Linux this is just the static
    ``linux_path`` match, already fully authoritative.

    Then falls back to the forms that are self-describing enough to match
    on any OS, no live state needed: a ``linux_path`` prefix (fixed and
    admin-assigned), a ``windows_path`` UNC prefix (carries its own host),
    and an ``smb://`` URL (likewise). A ``/Volumes/<name>`` mountpoint or a
    drive letter can't join them -- resolving either needs the live mount
    table, which only exists on the machine holding the mount.
    """
    if shares is None:
        shares = _load_shares()

    live_result = find_share_for_mounted_path(path_str, current_os(), shares)
    if live_result is not None:
        return live_result

    for matcher in (
        find_share_for_linux_mount,
        find_share_for_windows_unc,
        find_share_for_smb_url,
    ):
        result = matcher(path_str, shares)
        if result is not None:
            return result
    return None
