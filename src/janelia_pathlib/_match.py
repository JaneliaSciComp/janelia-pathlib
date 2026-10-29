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


def find_share_by_path_field(
    path_str: str, shares: Iterable[Share], attr: str
) -> tuple[Share, str] | None:
    """Find which of ``shares`` a path falls under by prefix-matching ``attr``.

    Works for the path fields that identify a share on their own, with no
    live state: ``linux_path`` (fixed and admin-assigned) and
    ``windows_path`` (a host-qualified UNC path). Since neither depends on
    the machine it's read on, both are worth trying regardless of which OS
    is running.
    """
    normalized = _to_forward_slash(path_str.strip())
    normalized = re.sub(r"/+$", "", normalized)

    for share in shares:
        candidate = _to_forward_slash(re.sub(r"[/\\]+$", "", getattr(share, attr) or ""))
        if not candidate or not normalized.startswith(candidate):
            continue
        # Only a match on a segment boundary: /labs/golf must not claim
        # /labs/golflab/data. No share nests under another (asserted at
        # load), so the first boundary match is the only one.
        rest = normalized[len(candidate) :]
        if rest == "" or rest.startswith("/"):
            return share, rest.lstrip("/")

    return None


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
    """Find which of ``shares`` a local mountpoint path points at.

    Only handles the two formats that *can't* be resolved from the string
    alone -- a macOS ``/Volumes/<name>`` mountpoint and a Windows drive
    letter. Both name a share indirectly, via per-machine state: macOS
    names mountpoints after the share name only, so two shares on
    different hosts collide and the second gets suffixed; a drive letter
    is an arbitrary per-user mapping. Only the live mount table resolves
    either, and only on the machine holding the mount -- hence ``os_name``.

    Returns (share, subpath), or None if nothing matches.
    """
    if os_name == "mac":
        return _macos.find_share_for_mac_path(path_str, shares)
    if os_name == "windows":
        return _windows.find_share_for_windows_drive(path_str, shares)
    return None


def find_share_for_janelia_uri(
    path_str: str, shares: Iterable[Share] | None = None
) -> tuple[Share, str] | None:
    """Match a ``janelia:/<linux_path>`` string against known shares.

    The URI body is a Linux path, so this is a ``linux_path`` prefix
    match like any other. A ``janelia://...`` string
    (an older, host-qualified form) is rejected: ``//`` would make the
    first path segment parse as a URI authority.
    """
    if shares is None:
        shares = load_shares()

    match = re.match(r"janelia:(/(?!/).*)$", path_str.strip())
    if match is None:
        return None
    return find_share_by_path_field(match.group(1), shares, "linux_path")


def find_share_for_path(
    path_str: str, shares: Iterable[Share] | None = None
) -> tuple[Share, str] | None:
    """Match a path string against known shares, in any OS's format.

    Returns (share, subpath) where subpath is the relative portion after
    the share mount prefix, or None if no share matches.

    A Linux path, a UNC path, and an ``smb://`` URL each name a share on
    their own, so all three are tried regardless of which OS is running --
    a UNC path passed in on Linux still resolves. Only a macOS
    ``/Volumes/<name>`` mountpoint and a Windows drive letter need the
    live mount table, so those are tried first and only on their own OS.
    """
    if shares is None:
        shares = load_shares()

    live_result = find_share_for_mounted_path(path_str, current_os(), shares)
    if live_result is not None:
        return live_result

    for attr in ("linux_path", "windows_path"):
        result = find_share_by_path_field(path_str, shares, attr)
        if result is not None:
            return result

    return find_share_for_smb_url(path_str, shares)
