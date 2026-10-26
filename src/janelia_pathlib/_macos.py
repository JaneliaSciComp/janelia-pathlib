from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ._share import find_share_by_host_and_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ._share import Share

_SMB_MOUNT_LINE = re.compile(r"^//(?:[^@]+@)?([^/]+)/(.+?) on (\S+) \(smbfs")

_ENV_MOUNT_TIMEOUT = "JANELIA_PATHLIB_MOUNT_TIMEOUT"
_DEFAULT_MOUNT_TIMEOUT = 30.0


def default_mount_timeout() -> float:
    """Seconds to wait for an on-demand mount, from ``JANELIA_PATHLIB_MOUNT_TIMEOUT``.

    The wait covers a native SMB credential prompt, so it budgets for a
    person typing a password -- raise it for a slow authenticator, or drop
    it to near-zero in headless contexts where no one is there to answer.
    """
    try:
        return float(os.environ.get(_ENV_MOUNT_TIMEOUT, _DEFAULT_MOUNT_TIMEOUT))
    except ValueError:
        return _DEFAULT_MOUNT_TIMEOUT


def _live_smb_mounts() -> list[tuple[str, str, str]]:
    """List live SMB mounts as ``(host, share_name, mountpoint)`` from `mount` output."""
    try:
        output = subprocess.run(["mount"], capture_output=True, text=True, check=True).stdout
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


def find_share_for_mac_path(path_str: str, shares: Iterable[Share]) -> tuple[Share, str] | None:
    """Find which of ``shares`` a ``/Volumes/<name>/...`` path points at.

    Disambiguates a bare ``/Volumes/<name>`` path when multiple shares would
    guess the same mountpoint name (e.g. two shares on different hosts that
    happen to share a name both default to ``/Volumes/<name>``, with the
    second one actually landing at ``/Volumes/<name>-1``) by checking which
    share's SMB host+name is actually mounted there right now.

    Returns (share, subpath), or None if the path isn't under a live mount.
    """
    normalized = re.sub(r"/+$", "", path_str.strip())
    parts = normalized.split("/")
    # /Volumes/<name>/rest... -> mountpoint is /Volumes/<name>
    if len(parts) < 3 or parts[1] != "Volumes":
        return None
    mountpoint = "/".join(parts[:3])

    for mount_host, mount_share, live_point in _live_smb_mounts():
        if live_point == mountpoint:
            share = find_share_by_host_and_name(mount_host, mount_share, shares)
            if share is not None:
                return share, normalized[len(mountpoint) :].lstrip("/")
    return None


def ensure_mounted(share_info: Share, *, timeout: float | None = None) -> Path | None:
    """Mount ``share_info`` on macOS if needed and return its real local mountpoint.

    Resolution is keyed on ``(host, share_name)`` from the share's ``mac_path``
    SMB URL, not on any ``/Volumes/<share>`` mountpoint name guessed from it,
    because macOS names mountpoints after the share name alone: two shares
    with the same name on different servers collide, and the second mounted
    gets suffixed (``/Volumes/<share>-1``).

    ``timeout`` defaults to ``default_mount_timeout()``.
    """
    if timeout is None:
        timeout = default_mount_timeout()
    host, share_name = share_info.host, share_info.share_name

    found = find_live_mount(host, share_name)
    if found is not None:
        return Path(found)

    # `open` goes through the native SMB client so it can use Keychain
    # credentials or prompt for them; unlike `mount_smbfs` it returns as
    # soon as the mount is *requested*. There's no handle to wait on, and
    # `mount` only prints a snapshot, so poll until the share shows up.
    subprocess.run(["open", share_info.mac_path], check=True)

    deadline = time.monotonic() + timeout
    while True:
        found = find_live_mount(host, share_name)
        if found is not None:
            return Path(found)
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)
