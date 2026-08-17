from __future__ import annotations

import shutil
from pathlib import Path

EXPECTED = {
    "W0": ((), (), (), ("W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "W10")),
    "W1": (("W0",), (), (), ("W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "W10")),
    "W2": (("W1",), (), ("W5",), ("W5", "W6", "W7", "W8", "W9", "W10")),
    "W3": (("W1",), (), (), ("W6", "W7", "W9", "W10")),
    "W4": (("W1",), (), (), ("W5", "W6", "W7", "W9", "W10")),
    "W5": (("W1",), (), ("W2",), ("W2", "W6", "W7", "W9", "W10")),
    "W6": (("W1",), ("W3", "W4", "W5"), (), ("W7", "W9", "W10")),
    "W7": (("W1",), ("W5", "W6", "W8"), (), ("W9", "W10")),
    "W8": (("W1",), (), (), ("W7", "W9", "W10")),
    "W9": (("W1",), ("W2", "W3", "W4", "W5", "W6", "W7", "W8"), (), ("W10",)),
    "W10": (("W9",), ("W0", "W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9"), (), ()),
}

REQUIRED_SECTIONS = (
    "研究目标",
    "适用边界",
    "依赖",
    "必须回答",
    "证据要求",
    "主张边界",
    "反证与冲突",
    "缺口与降级",
    "产物更新",
    "回访触发",
)


def _yaml_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(values) + "]"


def _work_package_text(
    package_id: str,
    *,
    kind: str = "core",
    coverage_role: str = "required",
    start_requires: tuple[str, ...] = (),
    finalize_requires: tuple[str, ...] = (),
    shared_baseline: tuple[str, ...] = (),
    may_reopen: tuple[str, ...] = (),
) -> str:
    fields = [
        "---",
        f"id: {package_id}",
        f"name: Work package {package_id}",
        f"kind: {kind}",
        "required_when: always",
        f"start_requires: {_yaml_list(start_requires)}",
        f"finalize_requires: {_yaml_list(finalize_requires)}",
        f"may_reopen: {_yaml_list(may_reopen)}",
        f"coverage_role: {coverage_role}",
    ]
    if shared_baseline:
        fields.append(f"shared_baseline: {_yaml_list(shared_baseline)}")
    fields.extend(["---", ""])
    body: list[str] = []
    for section in REQUIRED_SECTIONS:
        body.extend((f"## {section}", "内容"))
        if section == "回访触发":
            if may_reopen:
                body.extend(f"- {target}: 原因" for target in may_reopen)
            else:
                body.append("不适用")
    return "\n".join([*fields, *body, ""])


def _catalog_row(package_id: str, relative: str) -> str:
    return (
        f"| {package_id} | Work package {package_id} | required | always | "
        f"[{package_id}]({relative}) |"
    )


def build_contract_fixture(
    tmp_path: Path,
    *,
    mutation: str | None = None,
    official_fixture: str | None = None,
) -> tuple[Path, frozenset[str]]:
    root = tmp_path / "hetu-stock-analysis"
    core = root / "references" / "work-packages" / "core"
    core.mkdir(parents=True)
    root.joinpath("SKILL.md").write_text(
        "---\nname: hetu-stock-analysis\ndescription: synthetic contract\n---\n",
        encoding="utf-8",
    )

    files: dict[str, Path] = {}
    for package_id, metadata in EXPECTED.items():
        start_requires, finalize_requires, shared_baseline, may_reopen = metadata
        path = core / f"{package_id}-work.md"
        path.write_text(
            _work_package_text(
                package_id,
                start_requires=start_requires,
                finalize_requires=finalize_requires,
                shared_baseline=shared_baseline,
                may_reopen=may_reopen,
            ),
            encoding="utf-8",
        )
        files[package_id] = path

    if mutation == "unknown-dependency":
        files["W0"].write_text(
            _work_package_text("W0", start_requires=("W99",), may_reopen=EXPECTED["W0"][3]),
            encoding="utf-8",
        )
    elif mutation == "hard-cycle":
        files["W0"].write_text(
            _work_package_text("W0", finalize_requires=("W1",), may_reopen=EXPECTED["W0"][3]),
            encoding="utf-8",
        )
    elif mutation == "self-cycle":
        files["W0"].write_text(
            _work_package_text("W0", start_requires=("W0",), may_reopen=EXPECTED["W0"][3]),
            encoding="utf-8",
        )
    elif mutation == "asymmetric-baseline":
        files["W2"].write_text(
            _work_package_text("W2", start_requires=("W1",), may_reopen=EXPECTED["W2"][3]),
            encoding="utf-8",
        )
    elif mutation == "wrong-coverage-role":
        files["W0"].write_text(
            _work_package_text("W0", coverage_role="supplemental", may_reopen=EXPECTED["W0"][3]),
            encoding="utf-8",
        )
    elif mutation == "extra-frontmatter-field":
        files["W0"].write_text(
            files["W0"].read_text(encoding="utf-8").replace("---\n\n", "extra: no\n---\n\n", 1),
            encoding="utf-8",
        )
    elif mutation == "missing-required-frontmatter-field":
        files["W0"].write_text(
            files["W0"].read_text(encoding="utf-8").replace("name: Work package W0\n", "", 1),
            encoding="utf-8",
        )
    elif mutation == "wrong-frontmatter-type":
        files["W0"].write_text(
            files["W0"]
            .read_text(encoding="utf-8")
            .replace("start_requires: []", "start_requires: W0", 1),
            encoding="utf-8",
        )
    elif mutation == "filename-id-mismatch":
        mismatched = files["W0"].with_name("not-W0.md")
        files["W0"].rename(mismatched)
        files["W0"] = mismatched
    elif mutation == "filename-prefix-collision":
        mismatched = files["W1"].with_name("W10-not-W1.md")
        files["W1"].rename(mismatched)
        files["W1"] = mismatched
    elif mutation == "heading-only-in-code-fence":
        text = files["W0"].read_text(encoding="utf-8")
        text = text.replace("## 必须回答\n内容", "```markdown\n## 必须回答\n```", 1)
        files["W0"].write_text(text, encoding="utf-8")
    elif mutation == "heading-only-in-blockquote":
        text = files["W0"].read_text(encoding="utf-8")
        text = text.replace("## 必须回答\n内容", "> ## 必须回答\n> 内容", 1)
        files["W0"].write_text(text, encoding="utf-8")
    elif mutation == "missing-reopen-explanation":
        files["W0"].write_text(
            files["W0"].read_text(encoding="utf-8").replace("- W1: 原因\n", "", 1),
            encoding="utf-8",
        )
    elif mutation == "reopen-explanations-only-in-code-fence":
        explanations = "\n".join(f"- {target}: 原因" for target in EXPECTED["W0"][3])
        real_section = f"## 回访触发\n内容\n{explanations}"
        fenced_section = (
            f"```markdown\n## 回访触发\n{explanations}\n```\n\n"
            "## 回访触发\n内容"
        )
        files["W0"].write_text(
            files["W0"]
            .read_text(encoding="utf-8")
            .replace(real_section, fenced_section, 1),
            encoding="utf-8",
        )
    elif mutation == "extra-undeclared-reopen-target":
        files["W1"].write_text(
            files["W1"].read_text(encoding="utf-8")
            + "- W0: extra undeclared target\n",
            encoding="utf-8",
        )
    elif mutation == "empty-reopen-with-target":
        files["W10"].write_text(
            files["W10"].read_text(encoding="utf-8")
            + "- W0: contradicts empty may_reopen\n",
            encoding="utf-8",
        )
    elif mutation == "duplicate-reopen-target":
        files["W0"].write_text(
            files["W0"].read_text(encoding="utf-8")
            + "- W1: duplicate explanation\n",
            encoding="utf-8",
        )
    elif mutation == "nonempty-reopen-with-not-applicable":
        files["W0"].write_text(
            files["W0"].read_text(encoding="utf-8") + "\n不适用\n",
            encoding="utf-8",
        )
    elif mutation == "malformed-reopen-item":
        files["W0"].write_text(
            files["W0"].read_text(encoding="utf-8") + "- malformed item\n",
            encoding="utf-8",
        )
    elif mutation == "empty-reopen-explanation":
        files["W0"].write_text(
            files["W0"].read_text(encoding="utf-8") + "- W0:\n",
            encoding="utf-8",
        )
    elif mutation == "duplicate-frontmatter-reopen-target":
        files["W0"].write_text(
            files["W0"]
            .read_text(encoding="utf-8")
            .replace(
                "may_reopen: [W1, W2, W3, W4, W5, W6, W7, W8, W9, W10]",
                "may_reopen: [W1, W1, W2, W3, W4, W5, W6, W7, W8, W9, W10]",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation in {
        "ordered-reopen-explanations",
        "asterisk-reopen-explanations",
        "plus-reopen-explanations",
    }:
        markers = {
            "ordered-reopen-explanations": tuple(
                f"{index}." for index in range(1, len(EXPECTED["W0"][3]) + 1)
            ),
            "asterisk-reopen-explanations": ("*",) * len(EXPECTED["W0"][3]),
            "plus-reopen-explanations": ("+",) * len(EXPECTED["W0"][3]),
        }[mutation]
        text = files["W0"].read_text(encoding="utf-8")
        for marker, target in zip(markers, EXPECTED["W0"][3], strict=True):
            text = text.replace(f"- {target}: 原因", f"{marker} {target}: 原因", 1)
        files["W0"].write_text(text, encoding="utf-8")
    elif mutation == "nested-only-reopen-explanations":
        explanations = "\n".join(f"- {target}: 原因" for target in EXPECTED["W0"][3])
        nested = "\n".join(
            ["- W1: direct explanation"]
            + [f"  - {target}: nested explanation" for target in EXPECTED["W0"][3][1:]]
        )
        files["W0"].write_text(
            files["W0"].read_text(encoding="utf-8").replace(explanations, nested, 1),
            encoding="utf-8",
        )
    elif mutation == "empty-reopen-without-not-applicable":
        files["W0"].write_text(
            files["W0"]
            .read_text(encoding="utf-8")
            .replace("may_reopen: [W1, W2, W3, W4, W5, W6, W7, W8, W9, W10]", "may_reopen: []", 1),
            encoding="utf-8",
        )
    elif mutation == "duplicate-core-id":
        files["W1"].write_text(
            files["W1"].read_text(encoding="utf-8").replace("id: W1", "id: W0", 1),
            encoding="utf-8",
        )
    elif mutation == "unknown-shared-baseline":
        files["W0"].write_text(
            files["W0"]
            .read_text(encoding="utf-8")
            .replace("---\n\n", "shared_baseline: [W99]\n---\n\n", 1),
            encoding="utf-8",
        )
    elif mutation == "asymmetric-shared-baseline":
        files["W0"].write_text(
            files["W0"]
            .read_text(encoding="utf-8")
            .replace("---\n\n", "shared_baseline: [W1]\n---\n\n", 1),
            encoding="utf-8",
        )
    elif mutation == "self-shared-baseline":
        files["W0"].write_text(
            files["W0"]
            .read_text(encoding="utf-8")
            .replace("---\n\n", "shared_baseline: [W0]\n---\n\n", 1),
            encoding="utf-8",
        )

    catalog_rows = [
        _catalog_row(package_id, path.relative_to(root / "references" / "work-packages").as_posix())
        for package_id, path in files.items()
    ]
    catalog = root / "references" / "work-packages" / "catalog.md"

    official_paths: list[Path] = []
    if official_fixture:
        source = (
            Path(__file__).parents[1] / "fixtures" / "official_work_packages" / official_fixture
        )
        official = root / "references" / "work-packages" / "official"
        official.mkdir()
        for fixture in sorted(source.glob("*.md")):
            target = official / fixture.name
            shutil.copy2(fixture, target)
            official_paths.append(target)
        official_ids = {
            "invalid-official-id": "NOT-A-WX-PACKAGE",
            "multiword-official-id": "WX-RND-QUALITY-ASSURANCE",
            "lowercase-official-id": "WX-rnd-quality",
            "empty-official-domain": "WX--QUALITY",
            "empty-official-name": "WX-RND-",
        }
        if mutation in official_ids:
            original = official_paths[0]
            official_id = official_ids[mutation]
            renamed = original.with_name(f"{official_id}.md")
            original.rename(renamed)
            renamed.write_text(
                renamed.read_text(encoding="utf-8").replace(
                    "id: WX-RND-QUALITY", f"id: {official_id}", 1
                ),
                encoding="utf-8",
            )
            official_paths[0] = renamed
        if official_fixture != "unregistered":
            catalog_rows.extend(
                _catalog_row(
                    path.stem, path.relative_to(root / "references" / "work-packages").as_posix()
                )
                for path in official_paths
            )

    if mutation == "unknown-catalog-target":
        catalog_rows.append(_catalog_row("W99", "core/missing.md"))
    elif mutation == "duplicate-catalog-row":
        catalog_rows.append(catalog_rows[0])
    elif mutation == "catalog-row-without-link":
        catalog_rows[0] = "| W0 | Work package W0 | required | always |"
    elif mutation == "catalog-row-with-two-links":
        catalog_rows[0] = (
            "| W0 | Work package W0 | required | always | "
            "[W0](core/W0-work.md) [duplicate](core/W0-work.md) |"
        )
    elif mutation == "catalog-row-with-extra-cell":
        catalog_rows[0] = (
            "| W0 | Work package W0 | required | always | "
            "[W0](core/W0-work.md) | extra |"
        )
    elif mutation == "catalog-second-recognized-table":
        catalog_rows.extend(
            (
                "",
                "| ID | 名称 | 覆盖角色 | 适用条件 | 链接 |",
                "| --- | --- | --- | --- | --- |",
                _catalog_row("W0", "core/W0-work.md"),
            )
        )
    elif mutation == "catalog-traversal-link":
        catalog_rows[0] = _catalog_row("W0", "core/../core/W0-work.md")
    catalog_suffix = ""
    if mutation == "catalog-registration-only-in-code-fence":
        fenced_row = catalog_rows.pop(0)
        catalog_suffix = f"\n```markdown\n{fenced_row}\n```\n"
    catalog.write_text(
        "| ID | 名称 | 覆盖角色 | 适用条件 | 链接 |\n| --- | --- | --- | --- | --- |\n"
        + "\n".join(catalog_rows)
        + "\n"
        + catalog_suffix,
        encoding="utf-8",
    )

    manifest_files = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if official_fixture == "uncovered":
        manifest_files.remove(official_paths[0].relative_to(root).as_posix())
    if mutation == "manifest-path-mismatch":
        manifest_files.remove(files["W0"].relative_to(root).as_posix())
    return root, frozenset(manifest_files)
