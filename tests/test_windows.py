"""Tests for Windows drive-letter mapping.

These patch PowerShell's Get-SmbMapping output rather than requiring a real
mapped drive, so they run on any platform -- the same approach the macOS
`mount` tests in test_translate.py use.
"""

import json
import subprocess
import warnings
from unittest.mock import patch

import pytest

from janelia_pathlib import _share, _windows


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


def test_find_share_for_windows_drive_unmapped_warns():
    """A drive letter carries no share name, so there's nothing to match on --
    but a caller passing one here wants it translated, and silent passthrough
    hides why it wasn't."""
    mapping = json.dumps(
        [{"RemotePath": "\\\\fileserver.example.org\\alphalab", "LocalPath": "Z:"}]
    )
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        with pytest.warns(UserWarning, match="not a mapped network drive"):
            assert _windows.find_share_for_windows_drive("Q:", _share.load_shares()) is None


def test_find_share_for_windows_drive_unknown_share_returns_none():
    """A drive mapped to a share we don't know about isn't ours to translate --
    and unlike an unmapped letter, nothing is wrong, so it shouldn't warn."""
    mapping = json.dumps([{"RemotePath": "\\\\other.example.org\\nope", "LocalPath": "Z:"}])
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(mapping)):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert _windows.find_share_for_windows_drive("Z:", _share.load_shares()) is None


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


@pytest.mark.parametrize("stdout", ["", "   ", "not json at all"])
def test_live_windows_drives_degrades_on_bad_output(stdout):
    """Empty or unparsable PowerShell output yields no mappings, not a crash."""
    with patch("janelia_pathlib._windows.subprocess.run", return_value=_smb_mapping(stdout)):
        assert _windows._live_windows_drives() == []


def test_live_windows_drives_degrades_when_powershell_missing():
    """On a non-Windows machine there's no powershell at all -- OSError, not a crash."""
    with patch("janelia_pathlib._windows.subprocess.run", side_effect=OSError):
        assert _windows._live_windows_drives() == []
