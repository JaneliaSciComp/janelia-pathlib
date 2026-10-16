"""Fetch share data from the Fileglancer API and cache it locally.

The fetched shares.json is stored in the per-user cache directory so it
never lives in the source tree (which would leak internal Janelia paths
to a public repo). Invoked automatically on every use unless
JANELIA_PATHLIB_NO_FETCH is set (see ``_registry._load_shares``), and
manually via the ``janelia-paths-fetch`` CLI.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import platformdirs

API_URL = "https://fileglancer.int.janelia.org/api/file-share-paths"
_APP_NAME = "janelia-pathlib"
_SHARES_FILENAME = "shares.json"
_ENV_PATH = "JANELIA_PATHLIB_SHARES_PATH"
_ENV_NO_FETCH = "JANELIA_PATHLIB_NO_FETCH"
_ENV_TIMEOUT = "JANELIA_PATHLIB_FETCH_TIMEOUT"
_DEFAULT_TIMEOUT = 10.0


def resolve_cache_path() -> Path:
    """Path where the cached shares.json lives (env var overrides default).

    Env var JANELIA_PATHLIB_SHARES_PATH should be set to the json file name,
    not a parent directory.
    
    Returns:
        The full path to the json file where the shares should be written/read.
    """
    override = os.environ.get(_ENV_PATH)
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir(_APP_NAME)) / _SHARES_FILENAME


def no_fetch() -> bool:
    """Whether network auto-fetch is disabled via ``JANELIA_PATHLIB_NO_FETCH``.

    Useful in offline/CI environments with no access to the Janelia network:
    when set, the network is never touched -- the existing cache file is
    used as-is (however stale) if present, or share data degrades to "no
    known shares" (paths pass through untranslated) if not.
    """
    val = os.environ.get(_ENV_NO_FETCH, "").strip().lower()
    return val not in ("", "0", "false", "no")

def fetch_shares(cache_path: Path) -> None:
    """Fetch shares from the Fileglancer API and cache the response.

    Args:
        cache_path: The file path to cache the response to, as json,
            one entry per fileshare
    """
    try:
        timeout = float(os.environ.get(_ENV_TIMEOUT, _DEFAULT_TIMEOUT))
    except ValueError:
        timeout = _DEFAULT_TIMEOUT
    with urllib.request.urlopen(API_URL, timeout=timeout) as resp:
        data = json.loads(resp.read())

    paths = data.get("paths", data)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(paths, indent=2) + "\n")


def main() -> int:
    cache_path = resolve_cache_path()
    fetch_shares(cache_path)
    print(f"Wrote shares to {cache_path}")
    return 0
