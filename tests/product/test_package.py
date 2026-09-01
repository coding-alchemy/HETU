import tomllib
from importlib.metadata import version
from pathlib import Path

import hetu_stock


def test_package_version_matches_metadata() -> None:
    pyproject = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["version"] == "0.2.0"
    assert hetu_stock.__version__ == version("hetu-stock")
