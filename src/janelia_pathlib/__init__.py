from importlib.metadata import PackageNotFoundError, version

from janelia_pathlib._path import from_janelia_uri, to_janelia_uri

try:
    __version__ = version("janelia-pathlib")
except PackageNotFoundError:  # package is not installed
    __version__ = "uninstalled"

# Everything listed here becomes the public API and gets an API docs page.
# Implementation lives in underscore-prefixed modules; see CONTRIBUTING.md.
__all__ = [
    "__version__",
    "from_janelia_uri",
    "to_janelia_uri",
]
