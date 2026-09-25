"""Builders for third-party extension packages (phase 5, stage 06).

Each built package is a real directory on disk whose files and metadata can be
inspected independently; hostile bodies are never the test answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.product.skill.contract_fixtures import _work_package_text

ALLOWED_CAPABILITIES = ("local-files-read", "public-web-read")


def build_extension_package(
    root: Path,
    *,
    extension_id: str = "x.demo.rules",
    version: str = "0.1.0",
    summary: object = "演示扩展：行业比较规则",
    source: object = "用户本地资料包",
    capabilities: object = None,
    compat: object = "hetu-skill-v1",
    package_ids: tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
        ("x.demo.rules", (), (), ()),
    ),
    references: tuple[str, ...] = (),
    extra_files: tuple[str, ...] = (),
) -> Path:
    """Build a candidate extension package under ``root`` and return its path.

    ``package_ids`` entries are ``(wp_id, start_requires, finalize_requires,
    may_reopen)``.  ``extra_files`` are written but NOT declared in
    ``extension.json`` (the undeclared-file case).  ``summary``/``source``/
    ``capabilities``/``compat`` accept arbitrary objects to build broken
    metadata on purpose.
    """
    if capabilities is None:
        capabilities = ["local-files-read"]
    work_package_paths = []
    reference_paths = []
    for wp_id, start_requires, finalize_requires, may_reopen in package_ids:
        relative = f"work-packages/{wp_id}.md"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _work_package_text(
                wp_id,
                kind="third-party",
                coverage_role="supplemental",
                start_requires=start_requires,
                finalize_requires=finalize_requires,
                may_reopen=may_reopen,
            ),
            encoding="utf-8",
        )
        work_package_paths.append(relative)
    for relative in references:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 参考资料\n\n仅供人工阅读的规则说明。\n", encoding="utf-8")
        reference_paths.append(relative)
    for relative in extra_files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("undeclared\n", encoding="utf-8")
    metadata = {
        "id": extension_id,
        "version": version,
        "source": source,
        "summary": summary,
        "compatibility": compat,
        "capabilities": capabilities,
        "work_packages": work_package_paths,
        "references": reference_paths,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "extension.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root
