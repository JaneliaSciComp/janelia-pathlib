"""Tests for janelia-pathlib.

Tests run against a fictional share fixture (see tests/fixtures/shares.json
and tests/conftest.py). No real Janelia share data is referenced here.

Tests that exercise JaneliaPath (a pathlib.Path subclass) are restricted to
the OS whose path separators they expect, since pathlib always uses the
native separator regardless of mocking.
"""

import sys
from unittest.mock import patch
from urllib.error import URLError

import pytest

from janelia_pathlib import JaneliaPath, Share, _fetch, _registry, get_path, get_share, resolve, translate

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS paths")
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Linux paths")
unix_only = pytest.mark.skipif(sys.platform == "win32", reason="Unix paths")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")


# --- translate() tests ---
# These are pure string functions and run on every OS.


def test_translate_linux_to_mac():
    assert translate("/labs/alpha/alphalab/data", "mac") == "/Volumes/alphalab/data"


def test_translate_linux_to_windows():
    assert (
        translate("/labs/alpha/alphalab/data", "windows")
        == "\\\\fileserver.example.org\\alphalab\\data"
    )


def test_translate_mac_to_linux():
    assert translate("/Volumes/alphalab/data", "linux") == "/labs/alpha/alphalab/data"


def test_translate_windows_to_linux():
    assert (
        translate("\\\\fileserver.example.org\\alphalab\\data", "linux")
        == "/labs/alpha/alphalab/data"
    )


def test_translate_no_match():
    assert translate("/tmp/local/file.txt", "mac") is None


def test_translate_preserves_deep_subpath():
    assert (
        translate("/labs/bravo/bravolab/sub/dir/file.zarr", "mac")
        == "/Volumes/bravolab/sub/dir/file.zarr"
    )


def test_translate_share_root_only():
    assert translate("/labs/alpha/alphalab", "mac") == "/Volumes/alphalab"


def test_translate_scratch():
    assert translate("/scratch/delta/data", "mac") == "/Volumes/delta_scratch/data"


def test_translate_nearline_archive():
    result = translate("/archive/echo/data", "mac")
    assert result is not None
    assert result.startswith("/Volumes/")


# --- resolve() tests ---


def test_resolve_returns_share_and_subpath():
    result = resolve("/labs/alpha/alphalab/deep/nested/file.txt")
    assert result is not None
    share, subpath = result
    assert share.name == "Alpha"
    assert share.storage == "primary"
    assert subpath == "deep/nested/file.txt"


def test_resolve_no_match():
    assert resolve("/some/random/path") is None


def test_resolve_exact_share_root():
    result = resolve("/labs/alpha/alphalab")
    assert result is not None
    share, subpath = result
    assert subpath == ""


def test_resolve_longest_prefix_wins():
    """Both /labs/charlie and /labs/charlie/charlie match; the longer wins."""
    result = resolve("/labs/charlie/charlie/data")
    assert result is not None
    share, subpath = result
    assert share.linux_path == "/labs/charlie/charlie"
    assert share.storage == "primary"
    assert subpath == "data"


# --- JaneliaPath constructor tests ---


@mac_only
def test_janeliapath_translates_on_mac():
    p = JaneliaPath("/labs/charlie/charlie/data/file.txt")
    assert str(p) == "/Volumes/charlie/data/file.txt"


@linux_only
def test_janeliapath_translates_on_linux():
    p = JaneliaPath("/Volumes/alphalab/data")
    assert str(p) == "/labs/alpha/alphalab/data"


@unix_only
def test_janeliapath_no_match_passthrough():
    p = JaneliaPath("/tmp/local/file.txt")
    assert str(p) == "/tmp/local/file.txt"


@mac_only
def test_janeliapath_already_correct_os():
    """A Mac path on Mac should pass through unchanged."""
    p = JaneliaPath("/Volumes/alphalab/data")
    assert str(p) == "/Volumes/alphalab/data"


def test_janeliapath_is_path_subclass():
    from pathlib import Path

    p = JaneliaPath._from_raw("/labs/alpha/alphalab")
    assert isinstance(p, Path)
    assert isinstance(p, JaneliaPath)


@mac_only
def test_janeliapath_join_with_slash():
    p = JaneliaPath("/labs/bravo/bravolab") / "sub" / "data"
    assert str(p) == "/Volumes/bravolab/sub/data"
    assert isinstance(p, JaneliaPath)


@mac_only
def test_janeliapath_incremental_join_translates():
    """A partial prefix passes through, but joining to a full share triggers translation."""
    p = JaneliaPath("/labs/bravo")
    assert str(p) == "/labs/bravo"  # no match yet

    p = p / "bravolab"
    assert str(p) == "/Volumes/bravolab"  # now matches, translated


# --- to_os() tests ---


@unix_only
def test_to_os_mac_to_linux():
    p = JaneliaPath._from_raw("/Volumes/alphalab/data")
    linux = p.to_os("linux")
    assert str(linux) == "/labs/alpha/alphalab/data"
    assert isinstance(linux, JaneliaPath)


def test_to_os_mac_to_windows():
    p = JaneliaPath._from_raw("/Volumes/alphalab/data")
    win = p.to_os("windows")
    assert str(win) == "\\\\fileserver.example.org\\alphalab\\data"


def test_to_os_no_match_returns_self():
    p = JaneliaPath._from_raw("/tmp/local/file.txt")
    result = p.to_os("linux")
    assert result is p


# --- share property tests ---


@mac_only
def test_share_property():
    p = JaneliaPath("/labs/alpha/alphalab/experiment")
    share = p.share
    assert isinstance(share, Share)
    assert share.name == "Alpha"
    assert share.storage == "primary"
    assert share.linux_path == "/labs/alpha/alphalab"
    assert share.mac_path == "/Volumes/alphalab"


@mac_only
def test_share_property_no_match():
    p = JaneliaPath("/tmp/local/file.txt")
    assert p.share is None


# --- get_share() and from_share() tests ---
# get_share() returns a Share dataclass (no pathlib), runs everywhere.


def test_get_share():
    share = get_share("bravo", "primary")
    assert share.name == "Bravo"
    assert share.group == "bravo"
    assert share.storage == "primary"
    assert share.linux_path == "/labs/bravo/bravolab"


def test_get_share_home():
    share = get_share("bravo", "home")
    assert share.storage == "home"


def test_get_share_default_storage():
    share = get_share("alpha")
    assert share.storage == "primary"


def test_get_share_not_found():
    with pytest.raises(KeyError):
        get_share("nonexistent_group_xyz")


@mac_only
def test_from_share_on_mac():
    p = JaneliaPath.from_share("bravo")
    assert str(p) == "/Volumes/bravolab"
    assert isinstance(p, JaneliaPath)


@linux_only
def test_from_share_on_linux():
    p = JaneliaPath.from_share("bravo")
    assert str(p) == "/labs/bravo/bravolab"


@mac_only
def test_from_share_home():
    p = JaneliaPath.from_share("bravo", "home")
    assert str(p) == "/Volumes/bravo$"


@mac_only
def test_from_share_join_subpath():
    p = JaneliaPath.from_share("bravo") / "sub" / "data"
    assert str(p) == "/Volumes/bravolab/sub/data"


def test_from_share_not_found():
    with pytest.raises(KeyError):
        JaneliaPath.from_share("nonexistent_group_xyz")


# --- get_path() tests ---


@mac_only
def test_get_path_on_mac():
    p = get_path("bravo")
    assert str(p) == "/Volumes/bravolab"
    assert isinstance(p, JaneliaPath)


@mac_only
def test_get_path_with_storage():
    p = get_path("bravo", "home")
    assert str(p) == "/Volumes/bravo$"
    assert isinstance(p, JaneliaPath)


def test_get_path_not_found():
    with pytest.raises(KeyError):
        get_path("nonexistent_group_xyz")


# --- mount() tests ---


@patch("janelia_pathlib._path.subprocess.run")
@patch("janelia_pathlib._path.Path.exists", return_value=False)
@mac_only
def test_mount_calls_open_with_smb_url(mock_exists, mock_run):
    """mount() should call 'open smb://...' when share is not mounted."""
    # After subprocess.run, simulate the mount appearing
    mock_exists.side_effect = [False, False, True]
    p = JaneliaPath("/labs/alpha/alphalab/data")
    result = p.mount(timeout=2)
    assert result is True
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args[0] == "open"
    assert args[1].startswith("smb://")


@patch("janelia_pathlib._path.subprocess.run")
@patch("janelia_pathlib._path.Path.exists", return_value=True)
@mac_only
def test_mount_already_mounted(mock_exists, mock_run):
    """mount() should return True immediately if already mounted."""
    p = JaneliaPath("/labs/alpha/alphalab/data")
    result = p.mount()
    assert result is True
    mock_run.assert_not_called()


@linux_only
def test_mount_raises_on_linux():
    p = JaneliaPath._from_raw("/labs/alpha/alphalab/data")
    with pytest.raises(RuntimeError, match="only supported on macOS"):
        p.mount()


@mac_only
def test_mount_raises_for_unknown_path():
    p = JaneliaPath("/tmp/not/a/share")
    with pytest.raises(RuntimeError, match="No known share"):
        p.mount()


# --- Edge cases ---


def test_translate_trailing_slash():
    assert translate("/labs/alpha/alphalab/", "mac") == "/Volumes/alphalab"


def test_translate_windows_forward_slash():
    """Windows paths with forward slashes should still match."""
    assert (
        translate("//fileserver.example.org/alphalab/data", "linux")
        == "/labs/alpha/alphalab/data"
    )


# --- Offline / fetch robustness ---


def test_fetch_shares_uses_timeout(monkeypatch, tmp_path):
    """fetch_shares passes a timeout to urlopen so it can't hang indefinitely."""

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"paths": []}'

    def fake_urlopen(url, *args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    _fetch.fetch_shares(tmp_path / "shares.json")
    assert captured["timeout"] is not None
    assert captured["timeout"] > 0


def test_fetch_timeout_env_override(monkeypatch):

    monkeypatch.setenv("JANELIA_PATHLIB_FETCH_TIMEOUT", "3.5")
    assert _fetch.fetch_timeout() == 3.5
    monkeypatch.setenv("JANELIA_PATHLIB_FETCH_TIMEOUT", "not-a-number")
    assert _fetch.fetch_timeout() == _fetch._DEFAULT_TIMEOUT


def test_no_fetch_degrades_when_cache_missing(monkeypatch, tmp_path):
    """With JANELIA_PATHLIB_NO_FETCH set and no cache, load returns no shares and
    paths pass through untranslated instead of raising."""

    monkeypatch.setenv("JANELIA_PATHLIB_NO_FETCH", "1")
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(tmp_path / "missing.json"))
    _registry._load_shares.cache_clear()
    try:
        assert _registry._load_shares() == []
        # No known shares -> a real share path is left untranslated
        assert translate("/labs/alpha/alphalab/data", "mac") is None
    finally:
        _registry._load_shares.cache_clear()


def test_missing_cache_raises_without_no_fetch(monkeypatch, tmp_path):
    """Without the opt-out, a missing cache + failed fetch still raises (unchanged)."""
    monkeypatch.delenv("JANELIA_PATHLIB_NO_FETCH", raising=False)
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(tmp_path / "missing.json"))

    def boom(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    _registry._load_shares.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="auto-fetch failed"):
            _registry._load_shares()
    finally:
        _registry._load_shares.cache_clear()
