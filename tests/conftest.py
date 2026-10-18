"""Pytest configuration: point janelia-pathlib at a fake shares file."""

import os
from pathlib import Path

# Set before janelia_pathlib is imported by any test module so the registry
# loads from the fixture instead of the user cache dir (which may be empty
# or contain real Janelia share data we don't want tests to depend on).
os.environ["JANELIA_PATHLIB_SHARES_PATH"] = str(Path(__file__).parent / "fixtures" / "shares.json")

# _load_shares() refreshes from the network on every call by default; tests
# must never depend on (or overwrite) real network data, so disable fetching
# globally. Tests that specifically exercise fetch behavior override this
# themselves via monkeypatch.
os.environ["JANELIA_PATHLIB_NO_FETCH"] = "1"
