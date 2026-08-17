import re
import shutil
from pathlib import Path

import pytest

from hetu_stock.skill.package import SkillValidationError
from hetu_stock.skill.work_packages import (
    CORE_WORK_PACKAGE_IDS,
    validate_phase2_frontmatter,
    validate_work_package_contract,
)
from tests.product.skill.contract_fixtures import build_contract_fixture


def test_core_id_set_is_stable() -> None:
    assert CORE_WORK_PACKAGE_IDS == tuple(f"W{index}" for index in range(11))  # noqa: SIM300


@pytest.mark.parametrize(
    ("mutation", "stable_error_fragment"),
    [
        ("unknown-dependency", "unknown dependency target: W99"),
        ("hard-cycle", "dependency graph contains a cycle at W0"),
        ("self-cycle", "dependency graph contains a cycle at W0"),
        ("asymmetric-baseline", "shared baseline must be symmetric: W5 -> W2"),
        ("wrong-coverage-role", "core coverage contract violated: W0"),
        ("extra-frontmatter-field", "work-package fields do not match the closed contract"),
        (
            "missing-required-frontmatter-field",
            "work-package fields do not match the closed contract",
        ),
        ("wrong-frontmatter-type", "dependency fields must be string lists"),
        ("filename-id-mismatch", "filename does not match work-package ID: not-W0.md"),
        (
            "filename-prefix-collision",
            "filename does not match work-package ID: W10-not-W1.md",
        ),
        ("heading-only-in-code-fence", "missing sections: ['## 必须回答']"),
        ("heading-only-in-blockquote", "missing sections: ['## 必须回答']"),
        ("missing-reopen-explanation", "missing reopen explanation for W1"),
        (
            "empty-reopen-without-not-applicable",
            "empty may_reopen requires exact 不适用 line",
        ),
    ],
)
def test_invalid_work_package_graph_is_rejected(
    tmp_path: Path, mutation: str, stable_error_fragment: str
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path, mutation=mutation)
    with pytest.raises(ValueError, match=re.escape(stable_error_fragment)):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=0
        )


def test_fenced_reopen_explanations_do_not_satisfy_the_real_section(
    tmp_path: Path,
) -> None:
    root, manifest_files = build_contract_fixture(
        tmp_path, mutation="reopen-explanations-only-in-code-fence"
    )
    with pytest.raises(ValueError, match="reopen"):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=0
        )


def test_fenced_catalog_row_does_not_register_a_work_package(tmp_path: Path) -> None:
    root, manifest_files = build_contract_fixture(
        tmp_path, mutation="catalog-registration-only-in-code-fence"
    )
    with pytest.raises(ValueError, match="catalog"):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=0
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("catalog-row-without-link", "malformed catalog row"),
        ("catalog-row-with-two-links", "catalog file cell must contain exactly one link"),
        ("catalog-row-with-extra-cell", "malformed catalog row"),
        (
            "catalog-second-recognized-table",
            "catalog must contain exactly one registration table",
        ),
        ("catalog-traversal-link", "catalog target must be a local relative path"),
    ],
)
def test_catalog_rejects_every_malformed_registration_row(
    tmp_path: Path, mutation: str, message: str
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path, mutation=mutation)
    with pytest.raises(SkillValidationError, match=message):
        validate_work_package_contract(
            root,
            manifest_files=manifest_files,
            expected_official_count=0,
        )


@pytest.mark.parametrize(
    ("mutation", "stable_error_fragment"),
    [
        (
            "extra-undeclared-reopen-target",
            "undeclared reopen explanation target: W0",
        ),
        (
            "empty-reopen-with-target",
            "empty may_reopen must not contain reopen targets",
        ),
        ("duplicate-reopen-target", "duplicate reopen explanation for W1"),
        (
            "nonempty-reopen-with-not-applicable",
            "nonempty may_reopen must not contain 不适用",
        ),
        ("malformed-reopen-item", "malformed reopen explanation item"),
        ("empty-reopen-explanation", "malformed reopen explanation item"),
        (
            "duplicate-frontmatter-reopen-target",
            "may_reopen targets must be unique: W1",
        ),
    ],
)
def test_revisit_body_and_frontmatter_must_form_an_exact_contract(
    tmp_path: Path, mutation: str, stable_error_fragment: str
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path, mutation=mutation)
    with pytest.raises(ValueError, match=re.escape(stable_error_fragment)):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=0
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "ordered-reopen-explanations",
        "asterisk-reopen-explanations",
        "plus-reopen-explanations",
    ],
)
def test_direct_ordered_and_unordered_reopen_items_are_accepted(
    tmp_path: Path, mutation: str
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path, mutation=mutation)
    validate_work_package_contract(
        root, manifest_files=manifest_files, expected_official_count=0
    )


def test_nested_reopen_items_do_not_satisfy_direct_target_contract(
    tmp_path: Path,
) -> None:
    root, manifest_files = build_contract_fixture(
        tmp_path, mutation="nested-only-reopen-explanations"
    )
    with pytest.raises(ValueError, match="missing reopen explanation for W2"):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=0
        )


def test_valid_synthetic_contract_passes(tmp_path: Path) -> None:
    root, manifest_files = build_contract_fixture(tmp_path)
    validate_phase2_frontmatter(root)
    validate_work_package_contract(root, manifest_files=manifest_files, expected_official_count=0)


def test_registered_official_extension_fixture_passes(tmp_path: Path) -> None:
    root, manifest_files = build_contract_fixture(tmp_path, official_fixture="valid")
    validate_work_package_contract(root, manifest_files=manifest_files, expected_official_count=1)


@pytest.mark.parametrize(
    "relative",
    [
        "official/nested/WX-RND-QUALITY.md",
        "official/WX-RND-QUALITY.MD",
        "official/WX-RND-QUALITY.markdown",
    ],
)
def test_unexpected_official_file_cannot_bypass_zero_extension_contract(
    tmp_path: Path, relative: str
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path)
    source = (
        Path(__file__).parents[1]
        / "fixtures"
        / "official_work_packages"
        / "valid"
        / "WX-RND-QUALITY.md"
    )
    target = root / "references" / "work-packages" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest_files = manifest_files | {target.relative_to(root).as_posix()}

    with pytest.raises(ValueError, match="unexpected work-package"):
        validate_work_package_contract(
            root,
            manifest_files=frozenset(manifest_files),
            expected_official_count=0,
        )


def test_multiple_unexpected_work_package_paths_are_listed_line_by_line(
    tmp_path: Path,
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path)
    unexpected = (
        root / "references" / "work-packages" / "core" / "notes.txt",
        root / "references" / "work-packages" / "official" / "notes.txt",
    )
    for path in unexpected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unexpected", encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        validate_work_package_contract(
            root,
            manifest_files=manifest_files,
            expected_official_count=0,
        )

    assert str(caught.value) == (
        "unexpected work-package paths:\n"
        "- references/work-packages/core/notes.txt\n"
        "- references/work-packages/official/notes.txt"
    )


@pytest.mark.parametrize(
    ("official_fixture", "message", "expected_count"),
    [
        ("unregistered", "catalog", 1),
        ("uncovered", "manifest", 1),
        ("duplicate-id", "duplicate", 2),
    ],
)
def test_invalid_official_extension_fixture_is_rejected(
    tmp_path: Path, official_fixture: str, message: str, expected_count: int
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path, official_fixture=official_fixture)
    with pytest.raises(ValueError, match=message):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=expected_count
        )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("---\nname: 1\ndescription: valid\n---\n", "name"),
        ("---\nname: hetu-stock-analysis\ndescription: []\n---\n", "description"),
        ("---\n- not\n- a mapping\n---\n", "mapping"),
        ("---\nname: hetu-stock-analysis\ndescription: valid\n", "closed"),
    ],
)
def test_invalid_skill_frontmatter_is_rejected(tmp_path: Path, content: str, message: str) -> None:
    root, _ = build_contract_fixture(tmp_path)
    root.joinpath("SKILL.md").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        validate_phase2_frontmatter(root)


@pytest.mark.parametrize("description", ["   ", "x" * 1025])
def test_blank_or_overlong_skill_description_is_rejected(tmp_path: Path, description: str) -> None:
    root, _ = build_contract_fixture(tmp_path)
    root.joinpath("SKILL.md").write_text(
        f"---\nname: hetu-stock-analysis\ndescription: {description!r}\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="description"):
        validate_phase2_frontmatter(root)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate-core-id", "duplicate"),
        ("unknown-catalog-target", "catalog"),
        ("manifest-path-mismatch", "manifest"),
    ],
)
def test_specific_mechanical_contract_failures_have_stable_messages(
    tmp_path: Path, mutation: str, message: str
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path, mutation=mutation)
    with pytest.raises(ValueError, match=message):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=0
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown-shared-baseline", "shared baseline target"),
        ("asymmetric-shared-baseline", "shared baseline must be symmetric"),
        ("self-shared-baseline", "shared baseline must not reference itself"),
        ("duplicate-catalog-row", "duplicate catalog registration"),
    ],
)
def test_reviewed_mechanical_contract_gaps_are_rejected(
    tmp_path: Path, mutation: str, message: str
) -> None:
    root, manifest_files = build_contract_fixture(tmp_path, mutation=mutation)
    with pytest.raises(ValueError, match=message):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=0
        )


def test_official_extension_id_must_use_the_wx_namespace(tmp_path: Path) -> None:
    root, manifest_files = build_contract_fixture(
        tmp_path,
        mutation="invalid-official-id",
        official_fixture="valid",
    )
    with pytest.raises(ValueError, match="official extension ID"):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=1
        )


@pytest.mark.parametrize("mutation", ["multiword-official-id", "lowercase-official-id"])
def test_official_extension_id_allows_unrestricted_domain_and_name_tokens(
    tmp_path: Path, mutation: str
) -> None:
    root, manifest_files = build_contract_fixture(
        tmp_path,
        mutation=mutation,
        official_fixture="valid",
    )
    validate_work_package_contract(root, manifest_files=manifest_files, expected_official_count=1)


@pytest.mark.parametrize("mutation", ["empty-official-domain", "empty-official-name"])
def test_official_extension_id_requires_nonempty_domain_and_name(
    tmp_path: Path, mutation: str
) -> None:
    root, manifest_files = build_contract_fixture(
        tmp_path,
        mutation=mutation,
        official_fixture="valid",
    )
    with pytest.raises(ValueError, match="official extension ID"):
        validate_work_package_contract(
            root, manifest_files=manifest_files, expected_official_count=1
        )


def test_manifest_none_skips_only_manifest_coverage(tmp_path: Path) -> None:
    root, _ = build_contract_fixture(tmp_path)
    validate_work_package_contract(root, manifest_files=None, expected_official_count=0)


def test_empty_manifest_is_not_treated_as_skipped_coverage(tmp_path: Path) -> None:
    root, _ = build_contract_fixture(tmp_path)
    with pytest.raises(ValueError, match="manifest"):
        validate_work_package_contract(root, manifest_files=frozenset(), expected_official_count=0)
