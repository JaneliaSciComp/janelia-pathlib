from __future__ import annotations

import os
import re
import subprocess
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from ._share import find_share_by_host_and_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ._share import Share

_SMB_MOUNT_LINE = re.compile(r"^//(?:[^@]+@)?([^/]+)/(.+?) on (\S+) \(smbfs")

_ENV_MOUNT_TIMEOUT = "JANELIA_PATHLIB_MOUNT_TIMEOUT"
_ENV_NO_MOUNT = "JANELIA_PATHLIB_NO_MOUNT"
_DEFAULT_MOUNT_TIMEOUT = 30.0


def no_mount() -> bool:
    """Whether on-demand mounting is disabled via ``JANELIA_PATHLIB_NO_MOUNT``.

    Mounting a share can block on a credential prompt, which is unwanted in
    a headless or scripted context where nobody is there to answer. Whether
    that's the case is a property of the machine, not of the calling code,
    so it's an environment setting rather than an argument.
    """
    val = os.environ.get(_ENV_NO_MOUNT, "").strip().lower()
    return val not in ("", "0", "false", "no")


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

    # Not mounted, so it passes through untranslated -- indistinguishable to
    # the caller from a path that was never a share. If the mountpoint name
    # matches a known share, say so. macOS suffixes a colliding mountpoint
    # (/Volumes/foo-1), so match the stem too.
    mount_name = parts[2]
    stem = re.sub(r"-\d+$", "", mount_name)
    if any(share.share_name in (mount_name, stem) for share in shares):
        warnings.warn(
            f"{path_str!r} looks like Janelia share {stem!r}, but it isn't mounted "
            f"here, so it can't be translated. Mount it, or use from_janelia_uri() "
            f"to mount it on demand.",
            stacklevel=3,
        )
    return None


def ensure_mounted(share_info: Share, *, timeout: float | None = None) -> Path | None:
    """Mount ``share_info`` on macOS if needed and return its real local mountpoint.

    Resolution is keyed on ``(host, share_name)`` from the share's ``mac_path``
    SMB URL, not on any ``/Volumes/<share>`` mountpoint name guessed from it,
    because macOS names mountpoints after the share name alone: two shares
    with the same name on different servers collide, and the second mounted
    gets suffixed (``/Volumes/<share>-1``).

    ``timeout`` defaults to ``default_mount_timeout()``. Returns None if the
    share isn't mounted and ``JANELIA_PATHLIB_NO_MOUNT`` forbids mounting it.
    """
    if timeout is None:
        timeout = default_mount_timeout()
    host, share_name = share_info.host, share_info.share_name

    found = find_live_mount(host, share_name)
    if found is not None:
        return Path(found)

    if no_mount():
        return None

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
