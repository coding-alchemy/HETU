import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def source_facts():
    path = Path(__file__).resolve().parents[2] / "quant/tests/source_cases.py"
    spec = importlib.util.spec_from_file_location("kernel_source_facts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def data_wheelhouse(tmp_path_factory):
    """从已核缓存原字节构建当前源码发行包；不访问索引。"""
    import json
    import shutil
    import subprocess
    import sys
    import zipfile

    from pip._vendor.packaging.tags import parse_tag, sys_tags

    repo = Path(__file__).resolve().parents[2]
    target = tmp_path_factory.mktemp("data-wheelhouse")
    tags = set(sys_tags())
    index = json.loads((repo / ".superpowers/sdd/2026-11-04-06-data-kernel/offline-wheel-cache-index.json").read_text())
    for item in index["wheels"]:
        if not any(tags.intersection(parse_tag(tag)) for tag in item["tags"]):
            continue
        with zipfile.ZipFile(item["path"]) as wheel:
            info = next(name for name in wheel.namelist() if name.endswith(".dist-info/WHEEL"))
            metadata = wheel.read(info).decode()
            assert all("Tag: " + tag in metadata for tag in item["tags"])
            stem = info.split(".dist-info/")[0]
        tag = next(tag for tag in item["tags"] if tags.intersection(parse_tag(tag)))
        shutil.copyfile(item["path"], target / f"{stem}-{tag}.whl")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            str(repo / "data"),
            "-w",
            str(target),
        ],
        check=True,
    )
    return target


@pytest.fixture
def isolated_data_env(tmp_path, data_wheelhouse):
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    home = tmp_path / "venv"
    work = tmp_path / "outside"
    work.mkdir()
    subprocess.run([sys.executable, "-m", "venv", str(home)], check=True, env=env)
    python = home / "bin/python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--find-links",
            str(data_wheelhouse),
            "-c",
            str(Path(__file__).resolve().parents[1] / "requirements.lock"),
            "hetu-data==0.1.0",
        ],
        cwd=work,
        env=env,
        check=True,
    )
    subprocess.run([str(python), "-m", "pip", "--no-cache-dir", "check"], cwd=work, env=env, check=True)
    return dict(python=python, cli=home / "bin/hetu-data", workdir=work, wheelhouse=data_wheelhouse, env=env)
