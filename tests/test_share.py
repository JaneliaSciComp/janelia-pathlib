"""Tests for loading share data, and for the knobs that control it."""

import json
import subprocess
import warnings
from unittest.mock import patch
from urllib.error import URLError

import pytest
from conftest import mac_only

from janelia_pathlib import _fetch, _macos, _share, to_janelia_uri


def _share_entry(zone, linux_path, share_name):
    return {
        "zone": zone,
        "linux_path": linux_path,
        "windows_path": f"\\\\fileserver.example.org\\{share_name}",
        "mac_path": f"smb://fileserver.example.org/{share_name}",
    }


# --- nesting check ---


def test_load_shares_warns_on_nested_shares(monkeypatch, tmp_path):
    """First-match-wins is only unambiguous while no share sits inside
    another, so a nested pair in the API data should say so loudly rather
    than silently resolving to whichever came first."""
    cache_file = tmp_path / "shares.json"
    cache_file.write_text(
        json.dumps(
            [
                _share_entry("Nest", "/labs/outer", "outer"),
                _share_entry("Nest", "/labs/outer/inner", "inner"),
            ]
        )
    )
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(cache_file))
    _share.load_shares.cache_clear()
    try:
        with pytest.warns(UserWarning, match="nested inside"):
            _share.load_shares()
    finally:
        _share.load_shares.cache_clear()


def test_load_shares_quiet_for_sibling_shares():
    """Sibling shares under one group share a prefix but don't nest."""
    _share.load_shares.cache_clear()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _share.load_shares()
    finally:
        _share.load_shares.cache_clear()


# --- mount timeout ---


def test_default_mount_timeout_falls_back_without_env(monkeypatch):
    monkeypatch.delenv("JANELIA_PATHLIB_MOUNT_TIMEOUT", raising=False)
    assert _macos.default_mount_timeout() == 30.0


def test_default_mount_timeout_reads_env(monkeypatch):
    monkeypatch.setenv("JANELIA_PATHLIB_MOUNT_TIMEOUT", "90")
    assert _macos.default_mount_timeout() == 90.0


def test_default_mount_timeout_ignores_garbage(monkeypatch):
    """A malformed value shouldn't crash a path translation."""
    monkeypatch.setenv("JANELIA_PATHLIB_MOUNT_TIMEOUT", "soon please")
    assert _macos.default_mount_timeout() == 30.0


@pytest.mark.parametrize("value", ["1", "true", "yes", "anything"])
def test_no_mount_is_set(monkeypatch, value):
    monkeypatch.setenv("JANELIA_PATHLIB_NO_MOUNT", value)
    assert _macos.no_mount() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "  FALSE  "])
def test_no_mount_is_unset(monkeypatch, value):
    """Same falsey spellings JANELIA_PATHLIB_NO_FETCH accepts."""
    monkeypatch.setenv("JANELIA_PATHLIB_NO_MOUNT", value)
    assert _macos.no_mount() is False


def test_no_mount_defaults_to_false(monkeypatch):
    monkeypatch.delenv("JANELIA_PATHLIB_NO_MOUNT", raising=False)
    assert _macos.no_mount() is False


@mac_only
def test_ensure_mounted_honors_env_timeout(monkeypatch):
    """With a zero timeout, a share that never appears gives up immediately
    rather than waiting out the 30s default."""
    monkeypatch.setenv("JANELIA_PATHLIB_MOUNT_TIMEOUT", "0")
    share = next(s for s in _share.load_shares() if s.share_name == "alphalab")
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["mount"], returncode=0, stdout="")
        with patch("janelia_pathlib._macos.time.sleep") as mock_sleep:
            assert _macos.ensure_mounted(share) is None
            mock_sleep.assert_not_called()


# --- offline / fetch robustness ---


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
    _share.load_shares.cache_clear()
    try:
        with pytest.warns(UserWarning, match="No share data available"):
            assert _share.load_shares() == ()
        # No known shares -> a real share path is left untranslated
        assert to_janelia_uri("/labs/alpha/alphalab/data") == "/labs/alpha/alphalab/data"
    finally:
        _share.load_shares.cache_clear()


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
    _share.load_shares.cache_clear()
    try:
        with pytest.warns(UserWarning, match="No share data available"):
            assert _share.load_shares() == ()
    finally:
        _share.load_shares.cache_clear()


def test_load_shares_refetches_every_call(monkeypatch, tmp_path):
    """Without JANELIA_PATHLIB_NO_FETCH, load_shares() always attempts a fresh
    fetch -- not just when the cache file is missing."""
    monkeypatch.delenv("JANELIA_PATHLIB_NO_FETCH", raising=False)
    cache_file = tmp_path / "shares.json"
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(cache_file))

    call_count = {"n": 0}

    def fake_fetch_shares(path):
        call_count["n"] += 1
        path.write_text("[]")

    monkeypatch.setattr(_share, "fetch_shares", fake_fetch_shares)
    _share.load_shares.cache_clear()
    try:
        _share.load_shares()
        assert call_count["n"] == 1
        _share.load_shares.cache_clear()
        _share.load_shares()
        assert call_count["n"] == 2
    finally:
        _share.load_shares.cache_clear()


def test_fetch_failure_falls_back_to_stale_cache(monkeypatch, tmp_path):
    """If the network fetch fails but a cache file already exists, use the
    stale cache (with a warning) instead of raising."""
    monkeypatch.delenv("JANELIA_PATHLIB_NO_FETCH", raising=False)
    cache_file = tmp_path / "shares.json"
    cache_file.write_text(json.dumps([_share_entry("Alpha", "/labs/alpha/alphalab", "alphalab")]))
    monkeypatch.setenv("JANELIA_PATHLIB_SHARES_PATH", str(cache_file))

    def boom(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    _share.load_shares.cache_clear()
    try:
        with pytest.warns(UserWarning, match="stale"):
            shares = _share.load_shares()
        assert len(shares) == 1
        assert shares[0].zone == "Alpha"
    finally:
        _share.load_shares.cache_clear()
