import argparse
import subprocess
from unittest.mock import patch

import pytest
from conftest import mac_only

from janelia_pathlib import janelia_path


def _parser():
    parser = argparse.ArgumentParser(prog="demo", exit_on_error=False)
    parser.add_argument("--data", type=janelia_path)
    return parser


def _mount_output(mounted):
    stdout = "//u@fileserver.example.org/alphalab on /Volumes/alphalab (smbfs, nodev)"
    return subprocess.CompletedProcess(
        args=["mount"], returncode=0, stdout=stdout if mounted else ""
    )


@pytest.mark.parametrize(
    ("os_name", "expected"),
    [
        ("linux", "/labs/alpha/alphalab/data"),
        ("windows", "\\\\fileserver.example.org\\alphalab\\data"),
    ],
)
def test_janelia_path_converts_uri(os_name, expected):
    """Linux and Windows need no mounting, so a URI converts outright."""
    with patch("janelia_pathlib._translate.current_os", return_value=os_name):
        args = _parser().parse_args(["--data", "janelia:/labs/alpha/alphalab/data"])
        assert args.data == expected


def test_janelia_path_passes_through_plain_paths():
    """An argument declared with this type still accepts ordinary paths."""
    args = _parser().parse_args(["--data", "/tmp/local/x"])
    assert args.data == "/tmp/local/x"


def test_janelia_path_unknown_share_is_an_argument_error():
    """argparse renders ArgumentTypeError as `argument --data: ...`; the
    RuntimeError from_janelia_uri raises would escape as a traceback."""
    with pytest.raises(argparse.ArgumentError, match="No known share"):
        _parser().parse_args(["--data", "janelia:/nope/not-a-share"])


@mac_only
def test_janelia_path_resolves_mounted_share():
    with patch("janelia_pathlib._macos.subprocess.run", return_value=_mount_output(True)):
        args = _parser().parse_args(["--data", "janelia:/labs/alpha/alphalab/data"])
        assert args.data == "/Volumes/alphalab/data"


@mac_only
def test_janelia_path_mounts_on_demand():
    with patch("janelia_pathlib._macos.subprocess.run") as run:
        run.side_effect = [_mount_output(False), None, _mount_output(True)]
        args = _parser().parse_args(["--data", "janelia:/labs/alpha/alphalab/data"])
        assert args.data == "/Volumes/alphalab/data"
        assert run.call_args_list[1][0][0][0] == "open"


@mac_only
def test_janelia_path_mount_failure_is_an_argument_error():
    """A mount that never appears must surface as an argument error."""
    monkey = {"JANELIA_PATHLIB_MOUNT_TIMEOUT": "0"}
    with patch.dict("os.environ", monkey):
        with patch("janelia_pathlib._macos.subprocess.run", return_value=_mount_output(False)):
            with pytest.raises(argparse.ArgumentError, match="Failed to mount"):
                _parser().parse_args(["--data", "janelia:/labs/alpha/alphalab/data"])


@mac_only
def test_no_mount_env_turns_unmounted_share_into_an_error(monkeypatch):
    """The person running a script -- not its author -- knows whether there's
    anyone around to answer a credential prompt."""
    monkeypatch.setenv("JANELIA_PATHLIB_NO_MOUNT", "1")
    with patch("janelia_pathlib._macos.subprocess.run", return_value=_mount_output(False)) as run:
        with pytest.raises(argparse.ArgumentError, match="NO_MOUNT"):
            _parser().parse_args(["--data", "janelia:/labs/alpha/alphalab/data"])
        for call in run.call_args_list:
            assert call[0][0][0] != "open"


@mac_only
def test_no_mount_env_still_resolves_an_already_mounted_share(monkeypatch):
    """NO_MOUNT forbids mounting, not resolving."""
    monkeypatch.setenv("JANELIA_PATHLIB_NO_MOUNT", "1")
    with patch("janelia_pathlib._macos.subprocess.run", return_value=_mount_output(True)):
        args = _parser().parse_args(["--data", "janelia:/labs/alpha/alphalab/data"])
        assert args.data == "/Volumes/alphalab/data"
