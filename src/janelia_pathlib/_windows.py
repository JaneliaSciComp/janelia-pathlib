"""Windows-specific share resolution.

NOTE: implemented from documented PowerShell output format; not verified
against a real Windows machine.
"""

from __future__ import annotations

import json
import re
import subprocess
import warnings
from typing import TYPE_CHECKING

from ._share import find_share_by_host_and_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ._share import Share

_DRIVE_RE = re.compile(r"^([A-Za-z]:)(\\.*)?$")


def _live_windows_drives() -> list[tuple[str, str, str]]:
    """List live Windows SMB drive mappings as ``(host, share_name, drive)``.

    Uses PowerShell's ``Get-SmbMapping``, which returns structured,
    non-localized JSON -- unlike the legacy ``net use`` table, whose
    headers, status words, and column layout are locale-dependent and
    fragile to parse.
    """
    try:
        output = subprocess.run(
            ["powershell", "-Command", "Get-SmbMapping | ConvertTo-Json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return []

    if not output.strip():
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]

    drives = []
    for entry in data:
        remote = entry.get("RemotePath", "")
        local = entry.get("LocalPath", "")
        match = re.match(r"\\\\([^\\]+)\\(.+)", remote)
        if match and local:
            drives.append((match.group(1), match.group(2), local))
    return drives


def find_share_for_windows_drive(
    path_str: str, shares: Iterable[Share]
) -> tuple[Share, str] | None:
    """Find which of ``shares`` a mapped drive path (``X:\\...``) points at.

    A drive letter is an arbitrary per-user mapping that says nothing about
    the share on its own, so this checks which share is actually mapped to
    it right now.

    Returns (share, subpath), or None if the path isn't a mapped drive.
    """
    match = _DRIVE_RE.match(path_str.strip())
    if match is None:
        return None
    drive_norm = match.group(1).rstrip("\\").upper()
    subpath = (match.group(2) or "").replace("\\", "/").strip("/")

    for mount_host, mount_share, local in _live_windows_drives():
        if local.rstrip("\\").upper() == drive_norm:
            share = find_share_by_host_and_name(mount_host, mount_share, shares)
            if share is not None:
                return share, subpath
            # Mapped, but to a share we don't know -- not ours to translate.
            return None

    # A drive letter carries no share name, so unlike a /Volumes path there's
    # nothing to match against to tell a lapsed Janelia mapping from any other
    # local drive. Warn anyway: a caller passing a drive letter here is asking
    # for it to be translated, and silent passthrough hides why it wasn't.
    warnings.warn(
        f"{path_str!r} is not a mapped network drive, so it can't be "
        f"translated. Map it, or use from_janelia_uri() with the share's "
        f"janelia: URI.",
        stacklevel=3,
    )
    return None
