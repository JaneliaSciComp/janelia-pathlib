"""Pytest configuration: point janelia-pathlib at a fake shares file."""

import os
import sys
from pathlib import Path

import pytest

# Set before janelia_pathlib is imported by any test module so the registry
# loads from the fixture instead of the user cache dir (which may be empty
# or contain real Janelia share data we don't want tests to depend on).
os.environ["JANELIA_PATHLIB_SHARES_PATH"] = str(Path(__file__).parent / "fixtures" / "shares.json")

# load_shares() refreshes from the network on every call by default; tests
# must never depend on (or overwrite) real network data, so disable fetching
# globally. Tests that specifically exercise fetch behavior override this
# themselves via monkeypatch.
os.environ["JANELIA_PATHLIB_NO_FETCH"] = "1"

# Only for tests whose *output* is OS-specific. Tests that patch a live mount
# table run everywhere -- see test_windows.py.
mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS paths")
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Linux paths")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
