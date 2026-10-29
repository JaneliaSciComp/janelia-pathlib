"""Tests for the public to_janelia_uri / from_janelia_uri conversions.

Tests run against a fictional share fixture (see tests/fixtures/shares.json
and tests/conftest.py). No real Janelia share data is referenced here.
"""

import subprocess
import sys
import warnings
from unittest.mock import patch

import pytest
from conftest import linux_only, mac_only, windows_only

from janelia_pathlib import from_janelia_uri, to_janelia_uri

# --- to_janelia_uri(): formats that resolve on any OS ---


def test_to_janelia_uri_from_linux_path():
    assert to_janelia_uri("/labs/alpha/alphalab/data") == "janelia:/labs/alpha/alphalab/data"


def test_to_janelia_uri_from_windows_path():
    """A UNC path carries its own host, so it resolves without live state --
    including when running on a non-Windows machine."""
    assert (
        to_janelia_uri("\\\\fileserver.example.org\\alphalab\\data")
        == "janelia:/labs/alpha/alphalab/data"
    )


def test_to_janelia_uri_from_smb_url_on_any_os():
    """Same for an smb:// URL -- host and share name are both in the string."""
    assert (
        to_janelia_uri("smb://fileserver.example.org/alphalab/data")
        == "janelia:/labs/alpha/alphalab/data"
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


def test_to_janelia_uri_share_root_only():
    assert to_janelia_uri("/labs/alpha/alphalab") == "janelia:/labs/alpha/alphalab"


def test_to_janelia_uri_smb_url_share_root_only():
    assert to_janelia_uri("smb://fileserver.example.org/alphalab") == "janelia:/labs/alpha/alphalab"


def test_to_janelia_uri_smb_url_with_dollar_share():
    """Hidden shares (trailing $) are ordinary share names, not special syntax."""
    assert (
        to_janelia_uri("smb://fileserver.example.org/alpha$/data")
        == "janelia:/labs/alpha/home/data"
    )


def test_to_janelia_uri_trailing_slash():
    assert to_janelia_uri("/labs/alpha/alphalab/") == "janelia:/labs/alpha/alphalab"


def test_to_janelia_uri_windows_forward_slash():
    """Windows paths with forward slashes should still match."""
    assert (
        to_janelia_uri("//fileserver.example.org/alphalab/data")
        == "janelia:/labs/alpha/alphalab/data"
    )


def test_to_janelia_uri_no_match_passthrough():
    assert to_janelia_uri("/tmp/local/scratch/file.txt") == "/tmp/local/scratch/file.txt"


# --- to_janelia_uri(): prefix matching is per path segment ---


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


# --- to_janelia_uri(): macOS mountpoints need live state ---


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
    """An unmounted share is left unresolved rather than guessed from the
    static mac_path field -- a guess could be wrong in exactly the way the
    mount-name collision causes. But passing through silently looks identical
    to a path that was never a share, so say which it was."""
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


# --- from_janelia_uri() ---


def test_from_janelia_uri_no_match_passthrough():
    assert from_janelia_uri("/tmp/local/scratch/file.txt") == "/tmp/local/scratch/file.txt"


def test_from_janelia_uri_unknown_share_raises():
    with pytest.raises(RuntimeError, match="No known share"):
        from_janelia_uri("janelia:/nope/not-a-share/data")


def test_from_janelia_uri_rejects_double_slash_form():
    """``janelia://host/share/...`` is the old form. ``//`` opens a URI
    authority component, which a Linux path has no value for, so it's
    rejected rather than silently parsing ``labs`` as an authority."""
    with pytest.raises(RuntimeError, match="No known share"):
        from_janelia_uri("janelia://labs/alpha/alphalab/data")


@linux_only
def test_from_janelia_uri_on_linux():
    assert from_janelia_uri("janelia:/labs/alpha/alphalab/data") == "/labs/alpha/alphalab/data"


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


# --- round trip ---


def test_round_trip_through_janelia_uri():
    uri = to_janelia_uri("/labs/alpha/alphalab/data")
    # On Linux, from_janelia_uri needs no mounting and resolves purely from
    # static share data, so the round trip works without any live state.
    if sys.platform == "linux":
        assert from_janelia_uri(uri) == "/labs/alpha/alphalab/data"


def test_round_trip_non_janelia_path_is_untouched():
    p = "/tmp/local/scratch/file.txt"
    assert from_janelia_uri(to_janelia_uri(p)) == p
