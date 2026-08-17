from importlib.metadata import version

import hetu_stock


def test_package_version_matches_metadata() -> None:
    assert hetu_stock.__version__ == version("hetu-stock")
