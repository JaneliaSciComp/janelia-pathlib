from __future__ import annotations

from argparse import ArgumentTypeError

from ._translate import from_janelia_uri


def janelia_path(path_str: str) -> str:
    """An ``argparse`` ``type=`` converter that turns a ``janelia:`` URI into a local path.

    ::

        parser.add_argument("--data", type=janelia_path)

    Anything that isn't a ``janelia:`` URI is passed through unchanged, so
    an argument declared this way still accepts ordinary local paths.

    On macOS this mounts the share if it isn't already, which may block on
    a credential prompt during ``parse_args()``. Set
    ``JANELIA_PATHLIB_NO_MOUNT`` to turn that into an argument error
    instead -- useful in headless or scripted contexts where nobody is
    there to answer. Whether that applies is a property of the machine, not
    of the script, so it's an environment setting.

    Args:
        path_str: The argument value, passed by ``argparse``.

    Returns:
        A local path in this OS's format, or ``path_str`` unchanged if it
        isn't a ``janelia:`` URI.

    Raises:
        ArgumentTypeError: if the URI names no known share, or the share
            can't be mounted.
    """
    try:
        return from_janelia_uri(path_str)
    except RuntimeError as e:
        # argparse only renders ArgumentTypeError/ValueError/TypeError as a
        # clean "argument --x: ..." message; anything else is a traceback.
        raise ArgumentTypeError(str(e)) from e
