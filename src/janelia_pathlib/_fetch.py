"""Fetch share data from the Fileglancer API and cache it locally.

The fetched shares.json is stored in the per-user cache directory so it
never lives in the source tree (which would leak internal Janelia paths
to a public repo). Invoked automatically on first import when the cache
is missing, and manually via the ``janelia-paths-fetch`` CLI.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import platformdirs

API_URL = "https://fileglancer.int.janelia.org/api/file-share-paths"
_APP_NAME = "janelia-pathlib"
_SHARES_FILENAME = "shares.json"
_ENV_VAR = "JANELIA_PATHLIB_SHARES_PATH"


def default_cache_path() -> Path:
    """Path where the cached shares.json lives (env var overrides default)."""
    override = os.environ.get(_ENV_VAR)
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir(_APP_NAME)) / _SHARES_FILENAME


def _smb_to_volumes(smb_url: str) -> str:
    match = re.match(r"smb://[^/]+/(.+)", smb_url)
    if match:
        return "/Volumes/" + match.group(1)
    return smb_url


def fetch_shares(target_path: Path) -> None:
    """Fetch shares from the Fileglancer API and write them to target_path."""
    with urllib.request.urlopen(API_URL) as resp:
        data = json.loads(resp.read())

    paths = data.get("paths", data)

    for entry in paths:
        for key in ("mount_path", "linux_path", "mac_path", "windows_path"):
            val = entry.get(key)
            if val:
                entry[key] = re.sub(r"[/\\]+$", "", val)
        mac = entry.get("mac_path")
        if mac and mac.startswith("smb://"):
            entry["mac_smb_url"] = mac
            entry["mac_path"] = _smb_to_volumes(mac)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(paths, indent=2) + "\n")


def main() -> int:
    target = default_cache_path()
    fetch_shares(target)
    print(f"Wrote shares to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
