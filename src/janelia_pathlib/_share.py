from __future__ import annotations

import json
import platform
import re
import warnings
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from typing import TYPE_CHECKING
from urllib.error import URLError

from ._fetch import fetch_shares, no_fetch, resolve_cache_path

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_SMB_URL_RE = re.compile(r"smb://([^/]+)/(.+)")


def current_os() -> str:
    """Return the current OS as 'linux', 'mac', or 'windows'."""
    system = platform.system()
    os_name = {"Darwin": "mac", "Linux": "linux", "Windows": "windows"}.get(system)
    if os_name is None:
        raise RuntimeError(f"Unsupported platform: {system}")
    return os_name


def parse_smb_url(url: str) -> tuple[str, str] | None:
    """Split an ``smb://host/share/...`` URL into ``(host, rest)``.

    Returns None if ``url`` isn't a well-formed ``smb://`` URL.
    """
    match = _SMB_URL_RE.match(url.strip().rstrip("/"))
    if match is None:
        return None
    return match.group(1), match.group(2)


@dataclass(frozen=True, slots=True)
class Share:
    """A single file share entry, matching the Fileglancer API's own field names.

    ``host`` and ``share_name`` are parsed from ``mac_path`` once at load
    (see ``from_api_entry``) rather than on each lookup. Live mount tables
    on macOS and Windows identify a share by that pair.
    """

    zone: str
    linux_path: str
    windows_path: str
    mac_path: str
    host: str
    share_name: str

    @classmethod
    def from_api_entry(cls, entry: Mapping[str, str]) -> Share:
        """Build a Share from one Fileglancer API record.

        Raises:
            ValueError: if ``mac_path`` isn't a well-formed ``smb://`` URL.
        """
        mac_path = entry["mac_path"]
        parsed = parse_smb_url(mac_path)
        if parsed is None:
            raise ValueError(f"Malformed smb path for mac mounting: {mac_path}")
        host, share_name = parsed
        return cls(
            zone=entry["zone"],
            linux_path=entry["linux_path"],
            windows_path=entry["windows_path"],
            mac_path=mac_path,
            host=host,
            share_name=share_name,
        )

    @property
    def uri(self) -> str:
        """The share's canonical ``janelia:/<linux_path>`` identity."""
        return f"janelia:{self.linux_path}"


def _warn_if_nested(shares: tuple[Share, ...], attr: str, sep: str) -> None:
    """Warn if any share's ``attr`` path sits inside another share's.

    Matching takes the first share whose path prefixes the input, which is
    only unambiguous while no share nests under another. That holds across
    all live Fileglancer data (a share mounted inside another share's mount
    would be odd to administer), but it's an assumption about data we don't
    control, so check rather than trust.

    Sorting puts any nested path directly after the one containing it, so
    comparing neighbours is enough -- no quadratic scan over every pair.
    """
    paths = sorted({getattr(share, attr) for share in shares if getattr(share, attr)})
    for outer, inner in pairwise(paths):
        if inner.startswith(outer.rstrip(sep) + sep):
            warnings.warn(
                f"Share {inner!r} is nested inside {outer!r}; paths under it may "
                f"resolve to the wrong share.",
                stacklevel=2,
            )


def find_share_by_host_and_name(
    host: str, share_name: str, shares: Iterable[Share]
) -> Share | None:
    """Find the share served as ``share_name`` by ``host``.

    This is how live mount tables identify a share on macOS and Windows.
    """
    for share in shares:
        if (share.host, share.share_name) == (host, share_name):
            return share
    return None


@cache
def load_shares() -> tuple[Share, ...]:
    """Load share data, refreshing it from the network on every call unless
    JANELIA_PATHLIB_NO_FETCH is set.

    JANELIA_PATHLIB_NO_FETCH means "never touch the network": use the cache
    file as-is if present (however stale). Without it, a fetch failure
    (offline, DNS, etc.) falls back to the existing cache file instead.
    Either way, if there's no cache file to fall back to, this degrades to
    no known shares (with a warning) rather than raising -- paths just pass
    through untranslated instead of breaking the caller.
    """
    cache_file = resolve_cache_path()

    if not no_fetch():
        try:
            fetch_shares(cache_file)
        except (URLError, OSError) as e:
            if cache_file.is_file():
                warnings.warn(
                    f"Failed to refresh share data ({e}); using cached data "
                    f"at {cache_file}, which may be stale.",
                    stacklevel=2,
                )

    if not cache_file.is_file():
        warnings.warn(
            f"No share data available at {cache_file}; paths will not be "
            "translated. Run `janelia-paths-fetch` from a machine with "
            "access to the Janelia network to populate the cache.",
            stacklevel=2,
        )
        return ()

    data_text = cache_file.read_text()
    raw = json.loads(data_text)
    shares = tuple(Share.from_api_entry(entry) for entry in raw)

    _warn_if_nested(shares, "linux_path", "/")
    _warn_if_nested(shares, "windows_path", "\\")
    return shares
