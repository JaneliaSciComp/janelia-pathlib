import janelia_pathlib


def test_version():
    assert isinstance(janelia_pathlib.__version__, str)
