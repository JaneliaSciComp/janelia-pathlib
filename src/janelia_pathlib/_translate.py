from __future__ import annotations

from typing import TYPE_CHECKING

from ._macos import ensure_mounted
from ._match import resolve, resolve_janelia_uri
from ._share import current_os

if TYPE_CHECKING:
    from pathlib import Path


def from_janelia_uri(path_str: str, *, timeout: float = 30) -> str:
    """Translate a ``janelia:`` URI to a real, usable local path string.

    A plain path is returned unchanged. A ``janelia:/<linux_path>`` string
    is resolved to the matching Janelia share and, on macOS, mounted on
    demand if not already mounted -- returning its real local mountpoint
    rather than a guessed ``/Volumes/<share>`` path, which may not exist or
    may belong to a different, colliding share.
    """
    if not path_str.strip().startswith("janelia:"):
        return path_str

    result = resolve_janelia_uri(path_str)
    if result is None:
        raise RuntimeError(f"No known share for URI: {path_str}")
    share_info, subpath = result

    os_name = current_os()
    sep = "/"
    if os_name == "mac":
        mount_point = ensure_mounted(share_info, timeout=timeout)
        if mount_point is None:
            raise RuntimeError(f"Failed to mount share: {share_info.zone}")
        base = str(mount_point)
    elif os_name == "linux":
        base = share_info.linux_path
    else:
        # windows_path is a backslash-separated UNC path; joining the
        # subpath with "/" would produce a mixed-separator string.
        base = share_info.windows_path
        sep = "\\"

    return base if not subpath else base + sep + subpath.replace("/", sep)


def to_janelia_uri(path: str | Path) -> str:
    """Translate a path to its canonical ``janelia:`` URI form.

    If the path matches a known Janelia share, returns its unambiguous
    ``janelia:/<linux_path>`` identity -- safe to round-trip through
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
