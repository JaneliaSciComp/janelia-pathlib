from importlib.metadata import PackageNotFoundError, version

from janelia_pathlib._argparse import janelia_path
from janelia_pathlib._translate import from_janelia_uri, to_janelia_uri

try:
    __version__ = version("janelia-pathlib")
except PackageNotFoundError:  # package is not installed
    __version__ = "uninstalled"

# Everything listed here becomes the public API and gets an API docs page.
# Implementation lives in underscore-prefixed modules; see CONTRIBUTING.md.
__all__ = [
    "__version__",
    "from_janelia_uri",
    "janelia_path",
    "to_janelia_uri",
]
