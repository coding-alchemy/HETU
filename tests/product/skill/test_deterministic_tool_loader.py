from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from tests.product.skill import deterministic_tool_loader


def test_load_script_does_not_write_bytecode_next_to_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script_root = tmp_path / "scripts"
    script_root.mkdir()
    source = script_root / "check-run-artifacts.py"
    shutil.copy2(
        deterministic_tool_loader.SCRIPT_ROOT / source.name,
        source,
    )
    monkeypatch.setattr(deterministic_tool_loader, "SCRIPT_ROOT", script_root)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)

    deterministic_tool_loader.load_script(source.name)

    cache_path = Path(importlib.util.cache_from_source(str(source)))
    assert not cache_path.exists()
    assert not (script_root / "__pycache__").exists()
