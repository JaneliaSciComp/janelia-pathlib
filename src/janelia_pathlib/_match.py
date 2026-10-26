from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import _macos, _windows
from ._share import current_os, find_share_by_host_and_name, load_shares, parse_smb_url

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ._share import Share


def _to_forward_slash(path: str) -> str:
    return path.replace("\\", "/")


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
        shares = load_shares()

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
    host), and a Windows drive letter isn't in the share data at all --
    only live state can map it to a share. On Linux this is just the
    static ``linux_path`` match, already fully authoritative.

    Then falls back to the forms that are self-describing enough to match
    on any OS, no live state needed: a ``linux_path`` prefix (fixed and
    admin-assigned), a ``windows_path`` UNC prefix (carries its own host),
    and an ``smb://`` URL (likewise). A ``/Volumes/<name>`` mountpoint or a
    drive letter can't join them -- resolving either needs the live mount
    table, which only exists on the machine holding the mount.
    """
    if shares is None:
        shares = load_shares()

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
