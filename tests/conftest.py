"""Pytest configuration: point janelia-pathlib at a fake shares file."""

import os
from pathlib import Path

# Set before janelia_pathlib is imported by any test module so the registry
# loads from the fixture instead of the user cache dir (which may be empty
# or contain real Janelia share data we don't want tests to depend on).
os.environ["JANELIA_PATHLIB_SHARES_PATH"] = str(
    Path(__file__).parent / "fixtures" / "shares.json"
)
