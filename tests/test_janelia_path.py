"""Tests for janelia-pathlib.

Tests run against a fictional share fixture (see tests/fixtures/shares.json
and tests/conftest.py). No real Janelia share data is referenced here.
"""

import json
import subprocess
import sys
from unittest.mock import patch
from urllib.error import URLError

import pytest

from janelia_pathlib import _fetch, _registry, from_janelia_uri, to_janelia_uri

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS paths")
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Linux paths")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")


# --- to_janelia_uri() tests ---


def test_to_janelia_uri_from_linux_path():
    assert (
        to_janelia_uri("/labs/alpha/alphalab/data")
        == "janelia://fileserver.example.org/alphalab/data"
    )


@mac_only
def test_to_janelia_uri_from_mac_path():
    """Resolving a Mac path requires live mount state (mac_path alone could
    be ambiguous), so this must be live-checked, not a static guess."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["mount"],
            returncode=0,
            stdout="//user@fileserver.example.org/alphalab on /Volumes/alphalab (smbfs, nodev)",
        )
        assert (
            to_janelia_uri("/Volumes/alphalab/data")
            == "janelia://fileserver.example.org/alphalab/data"
        )


@windows_only
def test_to_janelia_uri_from_windows_path():
    assert (
        to_janelia_uri("\\\\fileserver.example.org\\alphalab\\data")
        == "janelia://fileserver.example.org/alphalab/data"
    )


def test_to_janelia_uri_share_root_only():
    assert to_janelia_uri("/labs/alpha/alphalab") == "janelia://fileserver.example.org/alphalab"


def test_to_janelia_uri_no_match_passthrough():
    assert to_janelia_uri("/tmp/local/scratch/file.txt") == "/tmp/local/scratch/file.txt"


def test_to_janelia_uri_longest_prefix_wins():
    """Both /labs/charlie and /labs/charlie/charlie match; the longer wins."""
    assert (
        to_janelia_uri("/labs/charlie/charlie/data")
        == "janelia://fileserver.example.org/charlie/data"
    )


def test_to_janelia_uri_trailing_slash():
    assert to_janelia_uri("/labs/alpha/alphalab/") == "janelia://fileserver.example.org/alphalab"


@windows_only
def test_to_janelia_uri_windows_forward_slash():
    """Windows paths with forward slashes should still match."""
    assert (
        to_janelia_uri("//fileserver.example.org/alphalab/data")
        == "janelia://fileserver.example.org/alphalab/data"
    )


@mac_only
def test_to_janelia_uri_resolves_colliding_mac_mount_via_live_state():
    """Two shares (nrs/nearline) both nominally want /Volumes/foxtrot on macOS.
    Static string matching alone can't tell them apart -- live `mount` output,
    which records the actual SMB host for each mountpoint, must be checked."""
    mount_output = (
        "//user@nrs.example.org/foxtrot on /Volumes/foxtrot (smbfs, nodev)\n"
        "//user@nearline.example.org/foxtrot on /Volumes/foxtrot-1 (smbfs, nodev)"
    )
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["mount"], returncode=0, stdout=mount_output
        )
        assert to_janelia_uri("/Volumes/foxtrot/data") == "janelia://nrs.example.org/foxtrot/data"
        assert (
            to_janelia_uri("/Volumes/foxtrot-1/data")
            == "janelia://nearline.example.org/foxtrot/data"
        )


@mac_only
def test_to_janelia_uri_mac_mount_not_live_passes_through_unchanged():
    """If live `mount` output has nothing at this mountpoint (e.g. not actually
    mounted, or `mount` unavailable), the path is left unresolved rather than
    guessed from the static mac_path field -- a guess here could be wrong in
    exactly the same way the original mount-name collision bug was."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["mount"], returncode=0, stdout="")
        assert to_janelia_uri("/Volumes/alphalab/data") == "/Volumes/alphalab/data"


# --- from_janelia_uri() tests ---


def test_from_janelia_uri_no_match_passthrough():
    assert from_janelia_uri("/tmp/local/scratch/file.txt") == "/tmp/local/scratch/file.txt"


@linux_only
def test_from_janelia_uri_on_linux():
    assert (
        from_janelia_uri("janelia://fileserver.example.org/alphalab/data")
        == "/labs/alpha/alphalab/data"
    )


def test_from_janelia_uri_unknown_share_raises():
    with pytest.raises(RuntimeError, match="No known share"):
        from_janelia_uri("janelia://unknown.example.org/nope/data")


@mac_only
def test_from_janelia_uri_mounts_on_demand():
    """from_janelia_uri should trigger `open smb://...` when the share isn't
    mounted yet, then resolve the real mountpoint from live `mount` output."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mount_output_empty = subprocess.CompletedProcess(args=["mount"], returncode=0, stdout="")
        mount_output_found = subprocess.CompletedProcess(
            args=["mount"],
            returncode=0,
            stdout="//user@fileserver.example.org/alphalab on /Volumes/alphalab (smbfs, nodev)",
        )
        mock_run.side_effect = [mount_output_empty, None, mount_output_found]
        result = from_janelia_uri("janelia://fileserver.example.org/alphalab/data", timeout=2)
        assert result == "/Volumes/alphalab/data"
        open_call = mock_run.call_args_list[1]
        args = open_call[0][0]
        assert args[0] == "open"
        assert args[1] == "smb://fileserver.example.org/alphalab"


@mac_only
def test_from_janelia_uri_already_mounted():
    """from_janelia_uri should return the existing mountpoint without calling open."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["mount"],
            returncode=0,
            stdout="//user@fileserver.example.org/alphalab on /Volumes/alphalab (smbfs, nodev)",
        )
        result = from_janelia_uri("janelia://fileserver.example.org/alphalab/data")
        assert result == "/Volumes/alphalab/data"
        for call in mock_run.call_args_list:
            assert call[0][0][0] != "open"


@mac_only
def test_from_janelia_uri_resolves_colliding_share_name():
    """When two shares share a mount-name (nrs/nearline foxtrot collision),
    from_janelia_uri must match on (host, share_name), not on the guessed
    /Volumes/<name> path."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["mount"],
            returncode=0,
            stdout=(
                "//user@nrs.example.org/foxtrot on /Volumes/foxtrot (smbfs, nodev)\n"
                "//user@nearline.example.org/foxtrot on /Volumes/foxtrot-1 (smbfs, nodev)"
            ),
        )
        assert from_janelia_uri("janelia://nrs.example.org/foxtrot/data") == "/Volumes/foxtrot/data"
        assert (
            from_janelia_uri("janelia://nearline.example.org/foxtrot/data")
            == "/Volumes/foxtrot-1/data"
        )


@linux_only
def test_from_janelia_uri_raises_on_linux_mount_failure():
    """Linux never mounts (fixed mount points), so this shouldn't try to."""
    assert (
        from_janelia_uri("janelia://fileserver.example.org/alphalab/data")
        == "/labs/alpha/alphalab/data"
    )


# --- round trip ---


def test_round_trip_through_janelia_uri():
    uri = to_janelia_uri("/labs/alpha/alphalab/data")
    # On Linux, from_janelia_uri needs no mounting and resolves purely from
    # static registry data, so the round trip works without any live state.
    if sys.platform == "linux":
        assert from_janelia_uri(uri) == "/labs/alpha/alphalab/data"


def test_round_trip_non_janelia_path_is_untouched():
    p = "/tmp/local/scratch/file.txt"
    assert from_janelia_uri(to_janelia_uri(p)) == p


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


def test_no_fetch_degrades_when_cache_missing(monkeypatch, tmp_path):
    """With JANELIA_PATHLIB_NO_FETCH set and no cache, load returns no shares and
    paths pass through untranslated instead of raising."""

    monkeypatch.setenv("JANELIA_PATHLIB_NO_FETCH", "1")
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(tmp_path / "missing.json"))
    _registry._load_shares.cache_clear()
    try:
        with pytest.warns(UserWarning, match="No share data available"):
            assert _registry._load_shares() == {}
        # No known shares -> a real share path is left untranslated
        assert to_janelia_uri("/labs/alpha/alphalab/data") == "/labs/alpha/alphalab/data"
    finally:
        _registry._load_shares.cache_clear()


def test_missing_cache_degrades_without_no_fetch(monkeypatch, tmp_path):
    """Without the opt-out, a missing cache + failed fetch still degrades to no
    known shares (with a warning) instead of raising -- same end state as the
    no_fetch case, just reached via a failed fetch attempt instead of an
    explicit opt-out."""
    monkeypatch.delenv("JANELIA_PATHLIB_NO_FETCH", raising=False)
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(tmp_path / "missing.json"))

    def boom(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    _registry._load_shares.cache_clear()
    try:
        with pytest.warns(UserWarning, match="No share data available"):
            assert _registry._load_shares() == {}
    finally:
        _registry._load_shares.cache_clear()


def test_load_shares_refetches_every_call(monkeypatch, tmp_path):
    """Without JANELIA_PATHLIB_NO_FETCH, _load_shares() always attempts a fresh
    fetch -- not just when the cache file is missing."""
    monkeypatch.delenv("JANELIA_PATHLIB_NO_FETCH", raising=False)
    cache_file = tmp_path / "shares.json"
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(cache_file))

    call_count = {"n": 0}

    def fake_fetch_shares(path):
        call_count["n"] += 1
        path.write_text("[]")

    monkeypatch.setattr(_registry, "fetch_shares", fake_fetch_shares)
    _registry._load_shares.cache_clear()
    try:
        _registry._load_shares.cache_clear()
        _registry._load_shares()
        assert call_count["n"] == 1
        _registry._load_shares.cache_clear()
        _registry._load_shares()
        assert call_count["n"] == 2
    finally:
        _registry._load_shares.cache_clear()


def test_fetch_failure_falls_back_to_stale_cache(monkeypatch, tmp_path):
    """If the network fetch fails but a cache file already exists, use the
    stale cache (with a warning) instead of raising."""
    monkeypatch.delenv("JANELIA_PATHLIB_NO_FETCH", raising=False)
    cache_file = tmp_path / "shares.json"
    cache_file.write_text(
        json.dumps(
            [
                {
                    "zone": "Alpha",
                    "linux_path": "/labs/alpha/alphalab",
                    "windows_path": "\\\\fileserver.example.org\\alphalab",
                    "mac_path": "smb://fileserver.example.org/alphalab",
                }
            ]
        )
    )
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(cache_file))

    def boom(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    _registry._load_shares.cache_clear()
    try:
        with pytest.warns(UserWarning, match="stale"):
            shares = _registry._load_shares()
        assert len(shares) == 1
        assert next(iter(shares.values())).zone == "Alpha"
    finally:
        _registry._load_shares.cache_clear()
