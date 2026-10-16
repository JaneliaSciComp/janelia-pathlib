"""Windows-specific share resolution.

NOTE: implemented from documented PowerShell output format; not verified
against a real Windows machine.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ._registry import Share

DRIVE_RE = re.compile(r"^([A-Za-z]:)(\\.*)?$")


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
    drive: str, shares: Mapping[tuple[str, str], Share]
) -> Share | None:
    """Find which of ``shares`` is actually mapped to Windows drive letter ``drive``."""
    drive_norm = drive.rstrip("\\").upper()
    for mount_host, mount_share, local in _live_windows_drives():
        if local.rstrip("\\").upper() == drive_norm:
            share = shares.get((mount_host, mount_share))
            if share is not None:
                return share
    return None
