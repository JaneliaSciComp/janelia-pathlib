"""Translation between local paths and the unambiguous janelia:// URI form."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ._registry import (
    Share,
    current_os,
    find_live_mount,
    resolve,
    resolve_janelia_url,
)


def _ensure_mounted(share_info: Share, *, timeout: float = 30) -> Path | None:
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

    if current_os() != "mac":
        raise RuntimeError("Automatic mounting is only supported on macOS")

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


def from_janelia_uri(path_str: str, *, timeout: float = 30) -> str:
    """Translate a ``janelia://`` URI to a real, usable local path string.

    A plain path is returned unchanged. A
    ``janelia://host/share/subpath`` string is resolved to the matching
    Janelia share and, on macOS, mounted on demand if not already mounted --
    returning its real local mountpoint rather than a guessed
    ``/Volumes/<share>`` path, which may not exist or may belong to a
    different, colliding share.
    """
    if not path_str.strip().startswith("janelia://"):
        return path_str

    result = resolve_janelia_url(path_str)
    if result is None:
        raise RuntimeError(f"No known share for URL: {path_str}")
    share_info, subpath = result

    if current_os() == "mac":
        mount_point = _ensure_mounted(share_info, timeout=timeout)
        if mount_point is None:
            raise RuntimeError(f"Failed to mount share: {share_info.zone}")
        base = str(mount_point)
    else:
        base = getattr(share_info, f"{current_os()}_path")

    return base if not subpath else base + "/" + subpath


def to_janelia_uri(path: str | Path) -> str:
    """Translate a path to its canonical ``janelia://`` URI form.

    If the path matches a known Janelia share, returns its unambiguous
    ``janelia://host/share/subpath`` identity -- safe to round-trip through
    ``from_janelia_uri`` on any OS, unlike a ``/Volumes/<share>`` mountpoint
    name, which can collide with another share's on macOS. Otherwise
    returns the plain path string unchanged.
    """
    result = resolve(str(path))
    if result is None:
        return str(path)
    share_info, subpath = result
    url = share_info.uri
    return url if not subpath else url + "/" + subpath

