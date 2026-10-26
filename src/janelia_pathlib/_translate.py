from __future__ import annotations

from typing import TYPE_CHECKING

from ._macos import ensure_mounted, no_mount
from ._match import find_share_for_janelia_uri, find_share_for_path
from ._share import current_os

if TYPE_CHECKING:
    from pathlib import Path


def from_janelia_uri(path_str: str, *, timeout: float | None = None) -> str:
    """Translate a ``janelia:`` URI to a real, usable local path string.

    The returned path is in the running OS's own format: a ``/Volumes``
    mountpoint on macOS, ``linux_path`` on Linux, a UNC path on Windows.

    On macOS the share is mounted on demand if it isn't already, which may
    trigger a credential prompt. The mountpoint is read back from the live
    mount table rather than guessed from the share name, since two shares
    with the same name on different hosts both want ``/Volumes/<name>``
    and the second one actually lands at ``/Volumes/<name>-1``. Linux and
    Windows need no mounting step -- shares are already at a fixed path or
    reachable directly by UNC path.

    Set ``JANELIA_PATHLIB_NO_MOUNT`` to never mount: an unmounted share
    raises instead. Useful where no one is there to answer a prompt.

    Args:
        path_str: A ``janelia:/<linux_path>`` URI. Any other string is
            returned unchanged, so this is safe to call on paths that may
            or may not be URIs.
        timeout: Seconds to wait for an on-demand macOS mount. Defaults to
            ``JANELIA_PATHLIB_MOUNT_TIMEOUT`` (30s), which budgets for
            someone typing a password. Ignored off macOS.

    Returns:
        A local path string, or ``path_str`` unchanged if it isn't a
        ``janelia:`` URI.

    Raises:
        RuntimeError: if the URI names no known share, if a macOS mount
            fails or times out, or if the share isn't mounted and
            ``JANELIA_PATHLIB_NO_MOUNT`` forbids mounting it.
    """
    if not path_str.strip().startswith("janelia:"):
        return path_str

    result = find_share_for_janelia_uri(path_str)
    if result is None:
        raise RuntimeError(f"No known share for URI: {path_str}")
    share_info, subpath = result

    os_name = current_os()
    sep = "/"
    if os_name == "mac":
        mount_point = ensure_mounted(share_info, timeout=timeout)
        if mount_point is None:
            if no_mount():
                raise RuntimeError(
                    f"Share {share_info.share_name!r} isn't mounted and "
                    f"JANELIA_PATHLIB_NO_MOUNT is set, so it can't be mounted."
                )
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

    The URI is built from the share's Linux path, which is fixed and
    admin-assigned, so it stays valid across machines and across a share
    moving to new hardware -- unlike a ``/Volumes/<share>`` mountpoint
    name, which is per-machine and can collide with another share's.
    Store or pass around the URI, and call ``from_janelia_uri`` at the
    point of use to get something the filesystem will accept.

    A Linux path, a Windows UNC path and an ``smb://`` URL each identify a
    share on their own, so all three are recognized on any OS. A macOS
    ``/Volumes`` mountpoint or a Windows drive letter names a share only
    via per-machine state, so those are recognized only while running on
    that OS with the share mounted, and pass through unchanged otherwise.

    Args:
        path: A local path in any of the formats above. Anything that
            doesn't match a known share is returned unchanged.

    Returns:
        A ``janelia:/<linux_path>`` URI, or ``str(path)`` unchanged if it
        doesn't fall under a known share.
    """
    result = find_share_for_path(str(path))
    if result is None:
        return str(path)
    share_info, subpath = result
    url = share_info.uri
    return url if not subpath else url + "/" + subpath
