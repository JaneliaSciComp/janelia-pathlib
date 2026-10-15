"""JaneliaPath: a pathlib.Path subclass with auto-translating Janelia file share paths."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from janelia_pathlib._registry import (
    Share,
    current_os,
    find_live_mount,
    get_share,
    janelia_url,
    resolve,
    resolve_janelia_url,
    smb_url_parts,
    translate,
)


def _ensure_mounted(share_info: Share, *, timeout: float = 30) -> Path | None:
    """Mount ``share_info`` on macOS if needed and return its real local mountpoint.

    Resolution is keyed on ``(host, share_name)`` from the share's SMB URL,
    not on the share's static ``mac_path`` guess, because macOS names
    mountpoints after the share name alone: two shares with the same name on
    different servers collide, and the second mounted gets suffixed
    (``/Volumes/<share>-1``).
    """
    smb_url = share_info.mac_smb_url
    if not smb_url:
        raise RuntimeError(f"No SMB URL available for share: {share_info.name}")

    parts = smb_url_parts(smb_url)
    if parts is None:
        raise RuntimeError(f"Malformed SMB URL for share: {share_info.name}")
    host, share_name = parts

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


class JaneliaPath(Path):
    """A Path that auto-translates Janelia file share paths to the current OS.

    Any path matching a known Janelia file share is automatically converted
    to the equivalent path on the current operating system.

    Examples:
        On Mac::

            >>> p = JaneliaPath('/labs/charlie/charlie/data')
            >>> str(p)
            '/Volumes/charlie/data'

        On Linux::

            >>> p = JaneliaPath('/Volumes/charlie/data')
            >>> str(p)
            '/labs/charlie/charlie/data'

    Paths that don't match any known share are passed through unchanged,
    behaving exactly like a normal ``pathlib.Path``.
    """

    _skip_translate: bool = False

    def __init__(self, *args: str | Path, **kwargs) -> None:
        if JaneliaPath._skip_translate:
            super().__init__(*args)
            return
        raw = Path(*args, **kwargs)
        translated = translate(str(raw))
        if translated is not None:
            super().__init__(translated)
        else:
            super().__init__(*args)

    @classmethod
    def _from_raw(cls, path_str: str) -> JaneliaPath:
        """Create a JaneliaPath without auto-translation."""
        cls._skip_translate = True
        try:
            return cls(path_str)
        finally:
            cls._skip_translate = False

    def to_os(self, target_os: str) -> JaneliaPath:
        """Return this path translated to a specific OS.

        Args:
            target_os: 'linux', 'mac', or 'windows'.

        Returns:
            A new JaneliaPath with the share prefix translated to the
            target OS. If the path doesn't match any share, returns
            itself unchanged.
        """
        result = translate(str(self), target_os=target_os)
        if result is None:
            return self
        return JaneliaPath._from_raw(result)

    @classmethod
    def from_share(cls, group: str, storage: str = "primary") -> JaneliaPath:
        """Construct a JaneliaPath from a group name and storage type.

        Args:
            group: The AD/unix group name (e.g. 'alpha', 'bravo').
            storage: The storage type (e.g. 'primary', 'home', 'scratch',
                'archive'). Defaults to 'primary'.

        Returns:
            A JaneliaPath for the share on the current OS.

        Raises:
            KeyError: If no share matches the given group and storage.
        """
        share = get_share(group, storage)
        path_str = getattr(share, f"{current_os()}_path")
        return cls._from_raw(path_str)

    @property
    def share(self) -> Share | None:
        """The matched Share entry, or None if this path doesn't match any share."""
        result = resolve(str(self))
        if result is None:
            return None
        return result[0]

    def mount(self, *, timeout: float = 30) -> Path | None:
        """Mount the file share for this path (macOS only).

        Uses the SMB URL to trigger macOS's native mount, then looks up the
        real local mountpoint from live ``mount`` output. This is necessary
        because macOS names mountpoints after the share name alone
        (``/Volumes/<share>``): two shares with the same name on different
        servers collide, and the second mounted gets suffixed
        (``/Volumes/<share>-1``). ``share_info.mac_path`` is only a guess and
        may not be where the share actually ends up.

        Args:
            timeout: Seconds to wait for the mount to appear. Defaults to 30.

        Returns:
            The real local mountpoint Path if the share is mounted (whether
            already mounted or freshly mounted by this call), or None if
            mounting did not succeed within the timeout.

        Raises:
            RuntimeError: If not on macOS or the share has no SMB URL.
        """
        share_info = self.share
        if share_info is None:
            raise RuntimeError(f"No known share for path: {self}")

        return _ensure_mounted(share_info, timeout=timeout)


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
            raise RuntimeError(f"Failed to mount share: {share_info.name}")
        base = str(mount_point)
    else:
        base = getattr(share_info, f"{current_os()}_path")

    return base if not subpath else base + "/" + subpath


def to_janelia_uri(path: str | Path) -> str:
    """Translate a path to its canonical ``janelia://`` URI form.

    If the path matches a known Janelia share, returns its unambiguous
    ``janelia://host/share/subpath`` identity -- safe to round-trip through
    ``from_janelia_uri`` on any OS, unlike a share's static ``mac_path``
    guess, which can collide with another share's on macOS. Otherwise
    returns the plain path string unchanged.
    """
    result = resolve(str(path))
    if result is None:
        return str(path)
    share_info, subpath = result
    url = janelia_url(share_info)
    if url is None:
        return str(path)
    return url if not subpath else url + "/" + subpath


def get_path(group: str, storage: str = "primary") -> JaneliaPath:
    """Get the JaneliaPath for a share by group name and storage type.

    Args:
        group: The AD/unix group name (e.g. 'alpha', 'bravo').
        storage: The storage type (e.g. 'primary', 'home', 'scratch',
            'archive'). Defaults to 'primary'.

    Returns:
        A JaneliaPath for the share on the current OS.

    Raises:
        KeyError: If no share matches the given group and storage.
    """
    return JaneliaPath.from_share(group, storage)
