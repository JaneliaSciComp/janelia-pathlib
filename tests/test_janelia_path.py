"""Tests for janelia-pathlib.

Tests run against a fictional share fixture (see tests/fixtures/shares.json
and tests/conftest.py). No real Janelia share data is referenced here.
"""

import json
import subprocess
import sys
import warnings
from unittest.mock import patch
from urllib.error import URLError

import pytest

from janelia_pathlib import _fetch, _macos, _share, _windows, from_janelia_uri, to_janelia_uri

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS paths")
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Linux paths")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")


# --- to_janelia_uri() tests ---


def test_to_janelia_uri_from_linux_path():
    assert to_janelia_uri("/labs/alpha/alphalab/data") == "janelia:/labs/alpha/alphalab/data"


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
        assert to_janelia_uri("/Volumes/alphalab/data") == "janelia:/labs/alpha/alphalab/data"


def test_to_janelia_uri_from_windows_path():
    """A UNC path carries its own host, so it resolves without live state --
    including when running on a non-Windows machine."""
    assert (
        to_janelia_uri("\\\\fileserver.example.org\\alphalab\\data")
        == "janelia:/labs/alpha/alphalab/data"
    )


def test_to_janelia_uri_share_root_only():
    assert to_janelia_uri("/labs/alpha/alphalab") == "janelia:/labs/alpha/alphalab"


def test_to_janelia_uri_from_smb_url_on_any_os():
    """Same for an smb:// URL -- host and share name are both in the string."""
    assert (
        to_janelia_uri("smb://fileserver.example.org/alphalab/data")
        == "janelia:/labs/alpha/alphalab/data"
    )


def test_to_janelia_uri_smb_url_share_root_only():
    assert to_janelia_uri("smb://fileserver.example.org/alphalab") == "janelia:/labs/alpha/alphalab"


def test_to_janelia_uri_smb_url_with_dollar_share():
    """Hidden shares (trailing $) are ordinary share names, not special syntax."""
    assert (
        to_janelia_uri("smb://fileserver.example.org/alpha$/data")
        == "janelia:/labs/alpha/home/data"
    )


@pytest.mark.parametrize("os_name", ["linux", "mac", "windows"])
@pytest.mark.parametrize(
    "path",
    [
        "/labs/alpha/alphalab/data",
        "\\\\fileserver.example.org\\alphalab\\data",
        "smb://fileserver.example.org/alphalab/data",
    ],
)
def test_self_describing_formats_resolve_on_every_os(path, os_name):
    """A linux path, a UNC path and an smb:// URL each name a share on
    their own, so none of them should depend on which OS is running."""
    with patch("janelia_pathlib._match.current_os", return_value=os_name):
        assert to_janelia_uri(path) == "janelia:/labs/alpha/alphalab/data"


def test_to_janelia_uri_no_match_passthrough():
    assert to_janelia_uri("/tmp/local/scratch/file.txt") == "/tmp/local/scratch/file.txt"


def test_to_janelia_uri_sibling_shares_under_one_group():
    """A group's shares sit side by side (/labs/charlie/home, .../charlie),
    never nested, so each resolves to itself."""
    assert to_janelia_uri("/labs/charlie/charlie/data") == "janelia:/labs/charlie/charlie/data"
    assert to_janelia_uri("/labs/charlie/home/data") == "janelia:/labs/charlie/home/data"


def test_to_janelia_uri_substring_share_name_is_not_a_prefix():
    """/labs/golf is a string prefix of /labs/golflab but not a path prefix:
    matching must respect segment boundaries, or every golflab path would
    resolve to the golf share."""
    assert to_janelia_uri("/labs/golflab/data") == "janelia:/labs/golflab/data"
    assert to_janelia_uri("/labs/golf/data") == "janelia:/labs/golf/data"
    assert to_janelia_uri("/labs/golflab") == "janelia:/labs/golflab"
    assert to_janelia_uri("/labs/golf") == "janelia:/labs/golf"


def test_to_janelia_uri_substring_share_name_unc():
    """Same boundary rule on the windows_path field."""
    assert (
        to_janelia_uri("\\\\fileserver.example.org\\golflab\\data") == "janelia:/labs/golflab/data"
    )


def test_to_janelia_uri_partial_segment_matches_nothing():
    """A path that shares a prefix with a share but diverges mid-segment
    belongs to no share at all -- it must not fall back to the shorter one."""
    assert to_janelia_uri("/labs/golfcourse/data") == "/labs/golfcourse/data"


def test_to_janelia_uri_trailing_slash():
    assert to_janelia_uri("/labs/alpha/alphalab/") == "janelia:/labs/alpha/alphalab"


def test_to_janelia_uri_windows_forward_slash():
    """Windows paths with forward slashes should still match."""
    assert (
        to_janelia_uri("//fileserver.example.org/alphalab/data")
        == "janelia:/labs/alpha/alphalab/data"
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
        assert to_janelia_uri("/Volumes/foxtrot/data") == "janelia:/nrs/foxtrot/data"
        assert to_janelia_uri("/Volumes/foxtrot-1/data") == "janelia:/nearline/foxtrot/data"


@mac_only
def test_to_janelia_uri_warns_when_known_share_is_unmounted():
    """An unmounted share passes through untranslated, which otherwise looks
    identical to a path that was never a share -- so say which it was."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["mount"], returncode=0, stdout="")
        with pytest.warns(UserWarning, match="isn't mounted"):
            assert to_janelia_uri("/Volumes/alphalab/data") == "/Volumes/alphalab/data"


@mac_only
def test_to_janelia_uri_warns_for_suffixed_collision_mountpoint():
    """macOS suffixes a colliding mountpoint, so /Volumes/foxtrot-1 should
    still be recognized as share `foxtrot`."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["mount"], returncode=0, stdout="")
        with pytest.warns(UserWarning, match="foxtrot"):
            to_janelia_uri("/Volumes/foxtrot-1/data")


@mac_only
def test_to_janelia_uri_quiet_for_non_janelia_volume():
    """A USB stick or disk image isn't ours -- no warning for /Volumes names
    that match no known share."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["mount"], returncode=0, stdout="")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert to_janelia_uri("/Volumes/MyBackupDrive/x") == "/Volumes/MyBackupDrive/x"


@mac_only
def test_to_janelia_uri_mac_mount_not_live_passes_through_unchanged():
    """If live `mount` output has nothing at this mountpoint (e.g. not actually
    mounted, or `mount` unavailable), the path is left unresolved rather than
    guessed from the static mac_path field -- a guess here could be wrong in
    exactly the same way the original mount-name collision bug was."""
    with patch("janelia_pathlib._macos.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["mount"], returncode=0, stdout="")
        with pytest.warns(UserWarning, match="isn't mounted"):
            assert to_janelia_uri("/Volumes/alphalab/data") == "/Volumes/alphalab/data"


# --- from_janelia_uri() tests ---


def test_from_janelia_uri_no_match_passthrough():
    assert from_janelia_uri("/tmp/local/scratch/file.txt") == "/tmp/local/scratch/file.txt"


@linux_only
def test_from_janelia_uri_on_linux():
    assert from_janelia_uri("janelia:/labs/alpha/alphalab/data") == "/labs/alpha/alphalab/data"


def test_from_janelia_uri_unknown_share_raises():
    with pytest.raises(RuntimeError, match="No known share"):
        from_janelia_uri("janelia:/nope/not-a-share/data")


def test_from_janelia_uri_rejects_double_slash_form():
    """``janelia://host/share/...`` is the old form. ``//`` opens a URI
    authority component, which a Linux path has no value for, so it's
    rejected rather than silently parsing ``labs`` as an authority."""
    with pytest.raises(RuntimeError, match="No known share"):
        from_janelia_uri("janelia://labs/alpha/alphalab/data")


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
        result = from_janelia_uri("janelia:/labs/alpha/alphalab/data", timeout=2)
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
        result = from_janelia_uri("janelia:/labs/alpha/alphalab/data")
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
        assert from_janelia_uri("janelia:/nrs/foxtrot/data") == "/Volumes/foxtrot/data"
        assert from_janelia_uri("janelia:/nearline/foxtrot/data") == "/Volumes/foxtrot-1/data"


@windows_only
def test_from_janelia_uri_on_windows():
    """Windows resolves to the UNC path -- no mounting step needed."""
    assert (
        from_janelia_uri("janelia:/labs/alpha/alphalab/data")
        == "\\\\fileserver.example.org\\alphalab\\data"
    )


@windows_only
def test_from_janelia_uri_windows_share_root_only():
    assert (
        from_janelia_uri("janelia:/labs/alpha/alphalab") == "\\\\fileserver.example.org\\alphalab"
    )


@windows_only
def test_from_janelia_uri_windows_nested_subpath_separators():
    """The subpath arrives forward-slashed from the matchers; every segment
    must come back backslash-separated, not just the first join."""
    assert (
        from_janelia_uri("janelia:/labs/alpha/alphalab/sub/deeper/data")
        == "\\\\fileserver.example.org\\alphalab\\sub\\deeper\\data"
    )


# --- Windows drive-letter mapping ---
#
# These patch PowerShell's Get-SmbMapping output rather than requiring a real
# mapped drive, so they run on any platform -- same approach as the macOS
# `mount` tests above.


def _smb_mapping(stdout):
    return subprocess.CompletedProcess(args=["powershell"], returncode=0, stdout=stdout)


def test_find_share_for_windows_drive_matches_mapping():
    mapping = json.dumps(
        [{"RemotePath": "\\\\fileserver.example.org\\alphalab", "LocalPath": "Z:"}]
    )
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        result = _windows.find_share_for_windows_drive("Z:\\data", _share.load_shares())
        assert result is not None
        share, subpath = result
        assert share.linux_path == "/labs/alpha/alphalab"
        assert subpath == "data"


def test_find_share_for_windows_drive_is_case_insensitive():
    """Drive letters are case-insensitive, and Get-SmbMapping may report a
    trailing backslash that the queried drive string lacks."""
    mapping = json.dumps(
        [{"RemotePath": "\\\\fileserver.example.org\\alphalab", "LocalPath": "Z:\\"}]
    )
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        result = _windows.find_share_for_windows_drive("z:", _share.load_shares())
        assert result is not None
        assert result[0].linux_path == "/labs/alpha/alphalab"


def test_find_share_for_windows_drive_resolves_colliding_share_names():
    """Two shares named `foxtrot` on different hosts are mapped to different
    letters; only the host in RemotePath tells them apart."""
    mapping = json.dumps(
        [
            {"RemotePath": "\\\\nrs.example.org\\foxtrot", "LocalPath": "X:"},
            {"RemotePath": "\\\\nearline.example.org\\foxtrot", "LocalPath": "Y:"},
        ]
    )
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        shares = _share.load_shares()
        assert _windows.find_share_for_windows_drive("X:", shares)[0].linux_path == "/nrs/foxtrot"
        assert (
            _windows.find_share_for_windows_drive("Y:", shares)[0].linux_path == "/nearline/foxtrot"
        )


def test_find_share_for_windows_drive_single_mapping_is_json_object():
    """ConvertTo-Json emits a bare object, not a list, for a single mapping."""
    mapping = json.dumps({"RemotePath": "\\\\fileserver.example.org\\alphalab", "LocalPath": "Z:"})
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        result = _windows.find_share_for_windows_drive("Z:", _share.load_shares())
        assert result is not None
        assert result[0].linux_path == "/labs/alpha/alphalab"


def test_find_share_for_windows_drive_unmapped_returns_none():
    mapping = json.dumps(
        [{"RemotePath": "\\\\fileserver.example.org\\alphalab", "LocalPath": "Z:"}]
    )
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        with pytest.warns(UserWarning, match="not a mapped network drive"):
            assert _windows.find_share_for_windows_drive("Q:", _share.load_shares()) is None


def test_find_share_for_windows_drive_ignores_non_drive_paths():
    """A UNC or linux path isn't a drive mapping -- that's the static
    matchers' job, so this shouldn't claim it."""
    mapping = json.dumps(
        [{"RemotePath": "\\\\fileserver.example.org\\alphalab", "LocalPath": "Z:"}]
    )
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        shares = _share.load_shares()
        assert _windows.find_share_for_windows_drive("/labs/alpha/alphalab", shares) is None
        assert (
            _windows.find_share_for_windows_drive("\\\\fileserver.example.org\\alphalab", shares)
            is None
        )


def test_find_share_for_windows_drive_unknown_share_returns_none():
    """A drive mapped to a share we don't know about isn't ours to translate --
    and unlike an unmapped letter, nothing is wrong, so it shouldn't warn."""
    mapping = json.dumps([{"RemotePath": "\\\\other.example.org\\nope", "LocalPath": "Z:"}])
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert _windows.find_share_for_windows_drive("Z:", _share.load_shares()) is None


@pytest.mark.parametrize("stdout", ["", "   ", "not json at all"])
def test_live_windows_drives_degrades_on_bad_output(stdout):
    """Empty or unparsable PowerShell output yields no mappings, not a crash."""
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(stdout)):
        assert _windows._live_windows_drives() == []


def test_live_windows_drives_degrades_when_powershell_missing():
    """On a non-Windows machine there's no powershell at all -- OSError, not a crash."""
    with patch("janelia_pathlib._windows.subprocess.run", side_effect=OSError):
        assert _windows._live_windows_drives() == []


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


def test_load_shares_warns_on_nested_shares(monkeypatch, tmp_path):
    """First-match-wins is only unambiguous while no share sits inside
    another, so a nested pair in the API data should say so loudly rather
    than silently resolving to whichever came first."""
    cache_file = tmp_path / "shares.json"
    cache_file.write_text(
        json.dumps(
            [
                {
                    "zone": "Nest",
                    "linux_path": "/labs/outer",
                    "windows_path": "\\\\fileserver.example.org\\outer",
                    "mac_path": "smb://fileserver.example.org/outer",
                },
                {
                    "zone": "Nest",
                    "linux_path": "/labs/outer/inner",
                    "windows_path": "\\\\fileserver.example.org\\inner",
                    "mac_path": "smb://fileserver.example.org/inner",
                },
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


def test_load_shares_quiet_for_sibling_shares(tmp_path):
    """Sibling shares under one group share a prefix but don't nest."""
    _share.load_shares.cache_clear()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _share.load_shares()
    finally:
        _share.load_shares.cache_clear()


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
    """Without JANELIA_PATHLIB_NO_FETCH, _load_shares() always attempts a fresh
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
        _share.load_shares.cache_clear()
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
    _share.load_shares.cache_clear()
    try:
        with pytest.warns(UserWarning, match="stale"):
            shares = _share.load_shares()
        assert len(shares) == 1
        assert shares[0].zone == "Alpha"
    finally:
        _share.load_shares.cache_clear()
