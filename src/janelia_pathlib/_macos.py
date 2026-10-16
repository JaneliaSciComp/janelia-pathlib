"""macOS-specific share resolution and on-demand mounting."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ._registry import Share

_SMB_MOUNT_LINE = re.compile(r"^//(?:[^@]+@)?([^/]+)/(.+?) on (\S+) \(smbfs")


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

    macOS assigns the mountpoint name (e.g. ``/Volumes/<name>``) based only
    on the share name, so two shares with the same name on different
    servers collide and the second gets suffixed (``/Volumes/<name>-1``).
    Matching on ``(host, share_name)`` against the live mount table is the
    only reliable way to find which one is actually ours.
    """
    for mount_host, mount_share, mountpoint in _live_smb_mounts():
        if mount_host == host and mount_share == share_name:
            return mountpoint
    return None


def find_share_for_mac_mountpoint(
    mountpoint: str, shares: Mapping[tuple[str, str], Share]
) -> Share | None:
    """Find which of ``shares`` is actually live-mounted at ``mountpoint``.

    Disambiguates a bare ``/Volumes/<name>`` path when multiple shares would
    guess the same mountpoint name (e.g. two shares on different hosts that
    happen to share a name both default to ``/Volumes/<name>``, with the
    second one actually landing at ``/Volumes/<name>-1``) by checking which
    share's SMB host+name is actually mounted there right now.
    """
    for mount_host, mount_share, live_point in _live_smb_mounts():
        if live_point == mountpoint:
            share = shares.get((mount_host, mount_share))
            if share is not None:
                return share
    return None


def ensure_mounted(share_info: Share, *, timeout: float = 30) -> Path | None:
    """Mount ``share_info`` on macOS if needed and return its real local mountpoint.

    Resolution is keyed on ``(host, share_name)`` from the share's ``mac_path``
    SMB URL, not on any ``/Volumes/<share>`` mountpoint name guessed from it,
    because macOS names mountpoints after the share name alone: two shares
    with the same name on different servers collide, and the second mounted
    gets suffixed (``/Volumes/<share>-1``).
    """
    host, share_name = share_info.get_host_and_share_name()
    smb_url = share_info.mac_path

    existing = find_live_mount(host, share_name)
    if existing is not None:
        return Path(existing)

    subprocess.run(["open", smb_url], check=True)

    # Wait for the mount to appear
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = find_live_mount(host, share_name)
        if found is not None:
            return Path(found)
        time.sleep(0.5)

    found = find_live_mount(host, share_name)
    return Path(found) if found is not None else None
