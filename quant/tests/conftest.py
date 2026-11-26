from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--factor-evidence-root", default=None)


@pytest.fixture
def factor_evidence_root(request):
    root = request.config.getoption("--factor-evidence-root")
    return Path(root) if root else None
