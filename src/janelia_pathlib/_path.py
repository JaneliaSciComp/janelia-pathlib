"""JaneliaPath: a pathlib.Path subclass with auto-translating Janelia file share paths."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from janelia_pathlib._registry import Share, current_os, get_share, resolve, translate


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

    def mount(self, *, timeout: float = 30) -> bool:
        """Mount the file share for this path (macOS only).

        Uses the SMB URL to trigger macOS's native mount. This is a no-op
        if the share is already mounted or if not running on macOS.

        Args:
            timeout: Seconds to wait for the mount to appear. Defaults to 30.

        Returns:
            True if the share mount point exists after the operation,
            False otherwise.

        Raises:
            RuntimeError: If not on macOS or the share has no SMB URL.
        """
        share_info = self.share
        if share_info is None:
            raise RuntimeError(f"No known share for path: {self}")

        mount_point = Path(share_info.mac_path)
        if mount_point.exists():
            return True

        if current_os() != "mac":
            raise RuntimeError("Automatic mounting is only supported on macOS")

        smb_url = share_info.mac_smb_url
        if not smb_url:
            raise RuntimeError(f"No SMB URL available for share: {share_info.name}")

        subprocess.run(["open", smb_url], check=True)

        # Wait for the mount to appear
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if mount_point.exists():
                return True
            time.sleep(0.5)

        return mount_point.exists()


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
