# 产品测试材料清理 01：自动测试材料去重

> 执行 Agent：使用 superpowers:executing-plans 按任务执行；步骤以复选框跟踪。
> 文档版本：v0.1
> 文档状态：已完成，复评通过（2026-10-11）
> 创建／修订日期：2026-10-10／2026-10-11
> 设计依据：[已批准设计](../../2026-10-10-product-fixtures-cleanup-design.md)
> 路线图及全局约束：[README](README.md)

**目标：** 官方扩展包共用一份基准，并行材料共用底层正文，原有正负例继续生效。

**结构：** 使用现有 build_contract_fixture 和 _load_fixture；不新增构造器。

**技术：** Python 标准库、pytest、现有 JSON／Markdown 材料。

## 1. 前置与文件

完整读取设计、路线图、contract_fixtures.py、test_work_package_contract.py、
test_phase5_parallel_contract.py 及本阶段涉及的 17 份 fixture。
文件路径均相对仓库根；136 份原文件的具体去向在路线图 §6。

| 文件 | 操作与责任 |
|---|---|
| tests/product/skill/contract_fixtures.py | 改为从 valid/WX-RND-QUALITY.md 生成四种环境 |
| tests/product/skill/test_work_package_contract.py | 保留原断言和直接复制场景 |
| tests/product/skill/test_phase5_parallel_contract.py | 新路径、按名称读取单份材料、检查答案隔离 |
| tests/product/fixtures/parallel/inputs/confluence.json | 新增：七个汇合场景，共用转载正文 |
| tests/product/fixtures/parallel/inputs/returns.json | 新增：四个返回场景，字段原值保持 |
| tests/product/fixtures/parallel/review-expectations.md | 移入原评审 README，更新当前相对定位 |
| official_work_packages 下四份冗余文件、原 phase5_parallel 下十二份文件 | 替代验证成立后按处置表删除 |

**接口：** build_contract_fixture 的参数、返回值及错误场景不变；
_load_fixture(relative: str) -> dict 继续接收 confluence/reprint-a.json 等旧逻辑名称，
返回独立、完整的原材料对象。后续阶段不依赖这两个接口的新参数。

## 2. 任务 1：官方扩展包一份基准

- [x] 读取四份相同文件与 COPY，核对副本只差 name 字段；保留 valid/WX-RND-QUALITY.md。
- [x] 在 test_work_package_contract.py 增加以下验证，只向临时材料根提供 valid 基准。
  原四份副本此时保留；旧构造器在临时环境不能形成相应合同错误，预期 FAIL。

~~~python
from tests.product.skill import contract_fixtures


@pytest.mark.parametrize(
    "variant,message,count",
    [("unregistered", "catalog", 1), ("uncovered", "manifest", 1),
     ("duplicate-id", "duplicate", 2)],
)
def test_negative_packages_need_only_the_valid_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str, message: str, count: int
) -> None:
    source = Path(contract_fixtures.__file__).parents[1] / "fixtures/official_work_packages/valid"
    product = tmp_path / "product"
    shutil.copytree(source, product / "fixtures/official_work_packages/valid")
    monkeypatch.setattr(contract_fixtures, "__file__", str(product / "skill/contract_fixtures.py"))
    root, manifest = build_contract_fixture(tmp_path / "run", official_fixture=variant)
    with pytest.raises(ValueError, match=message):
        validate_work_package_contract(root, manifest_files=manifest, expected_official_count=count)
~~~

~~~bash
.venv/bin/python -m pytest -q tests/product/skill/test_work_package_contract.py::test_negative_packages_need_only_the_valid_original
~~~

导入放在现有 imports，测试追加到现有负例之后；已有断言全部保留。
预期旧实现不能只靠 valid 基准生成三类环境，不通过修改错误期望使其变绿。

- [x] 替换 contract_fixtures.py 中 if official_fixture 分支从 source 定义至
  official_ids 定义之前的代码，保留后续 ID mutation、catalog 和 manifest 处理：

~~~python
        source = (
            Path(__file__).parents[1]
            / "fixtures"
            / "official_work_packages"
            / "valid"
            / "WX-RND-QUALITY.md"
        )
        official = root / "references" / "work-packages" / "official"
        official.mkdir()
        target = official / source.name
        shutil.copy2(source, target)
        official_paths.append(target)
        if official_fixture == "duplicate-id":
            duplicate = official / "WX-RND-QUALITY-COPY.md"
            duplicate.write_text(
                source.read_text(encoding="utf-8").replace(
                    "name: Research quality extension",
                    "name: Research quality extension copy",
                    1,
                ),
                encoding="utf-8",
            )
            official_paths.append(duplicate)
~~~

- [x] 运行整个 test_work_package_contract.py；预期退出 0。合法包通过，三个负例仍因
  原合同错误失败，ID mutation 与直接复制场景全部保持。
- [x] 验证通过后删除四份副本，保留 valid 基准；重跑上面的三类临时环境验证：

~~~python
from pathlib import Path

root = Path("tests/product/fixtures/official_work_packages")
for relative in (
    "unregistered/WX-RND-QUALITY.md",
    "uncovered/WX-RND-QUALITY.md",
    "duplicate-id/WX-RND-QUALITY.md",
    "duplicate-id/WX-RND-QUALITY-COPY.md",
):
    (root / relative).unlink()
~~~

## 3. 任务 2：并行材料共用正文

- [x] 在 test_phase5_parallel_contract.py 增加以下验证并运行单项；新 JSON 尚不存在时
  预期 FAIL。现有材料行为断言不删除、不降低强度。

~~~python
def test_reprints_share_one_stored_original() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures/parallel/inputs/confluence.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["bases"]) == 1
    assert "underlying_text" in data["bases"]["reprint"]
    for name in ("reprint-a", "reprint-b"):
        assert data["cases"][name]["base"] == "reprint"
        assert "underlying_text" not in data["cases"][name]["fields"]
    a, b = _load_fixture("confluence/reprint-a.json"), _load_fixture("confluence/reprint-b.json")
    assert a["underlying_text"] == b["underlying_text"]
    assert a["outlet"] != b["outlet"]
~~~

~~~bash
.venv/bin/python -m pytest -q tests/product/skill/test_phase5_parallel_contract.py::test_reprints_share_one_stored_original
~~~

- [x] 执行以下一次性转换。它读取原文件、核对共用字段，再产生新数据；不进入产品代码。

~~~python
import json
from pathlib import Path

old = Path("tests/product/fixtures/phase5_parallel")
new = Path("tests/product/fixtures/parallel")
(new / "inputs").mkdir(parents=True, exist_ok=True)
for source_group, target in (("confluence", "confluence"), ("violations", "returns")):
    original = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((old / source_group).glob("*.json"))
    }
    cases = {name: {"fields": payload} for name, payload in original.items()}
    bases = {}
    if source_group == "confluence":
        fields = (
            "source_type", "underlying_text_id", "underlying_text",
            "metric", "value", "unit", "event_date",
        )
        base = {key: original["reprint-a"][key] for key in fields}
        assert all(original["reprint-b"][key] == value for key, value in base.items())
        bases["reprint"] = base
        for name in ("reprint-a", "reprint-b"):
            cases[name] = {
                "base": "reprint",
                "fields": {
                    key: value for key, value in original[name].items() if key not in fields
                },
            }
    data = {"bases": bases, "cases": cases}
    for name, entry in cases.items():
        restored = {**bases.get(entry.get("base"), {}), **entry["fields"]}
        assert restored == original[name], name
    (new / "inputs" / f"{target}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
review = (old / "review-expectations/README.md").read_text(encoding="utf-8")
review = review.replace("# phase5_parallel 独立评审期望", "# 并行材料独立评审期望")
review = review.replace(
    "tests/product/fixtures/phase5_parallel/", "tests/product/fixtures/parallel/"
)
review = review.replace("本目录保存", "本文件保存").replace("不读取本目录", "不读取本评审文件")
review += "\n以下 confluence/*.json、violations/*.json 为逻辑选择名，由测试的 " \
          "_load_fixture 从 inputs/confluence.json、inputs/returns.json 读取当次对象。\n"
(new / "review-expectations.md").write_text(review, encoding="utf-8")
~~~

- [x] 将 FIXTURES 改为 fixtures/parallel；替换 _load_fixture 为下面完整实现：
  测试文件开头说明中的 tests/product/fixtures/phase5_parallel/ 同步改为 parallel/。

~~~python
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "parallel"


def _load_fixture(relative: str) -> dict:
    group, filename = relative.split("/", 1)
    dataset = {"confluence": "confluence", "violations": "returns"}[group]
    data = json.loads(
        (FIXTURES / "inputs" / f"{dataset}.json").read_text(encoding="utf-8")
    )
    entry = data["cases"][Path(filename).stem]
    return {**data["bases"].get(entry.get("base"), {}), **entry["fields"]}
~~~

- [x] 替换 test_review_expectations_live_only_on_the_review_side 为下段。
  评审依据由目录变为单文件，因此“不可读”限定同步指向本评审文件，原评分和输入隔离保持。

~~~python
def test_review_expectations_live_only_on_the_review_side() -> None:
    readme = (FIXTURES / "review-expectations.md").read_text(encoding="utf-8")
    assert "仅评审侧" in readme
    assert "不读取本评审文件" in readme
    for group, dataset in (("confluence", "confluence"), ("violations", "returns")):
        data = json.loads(
            (FIXTURES / "inputs" / f"{dataset}.json").read_text(encoding="utf-8")
        )
        for name in data["cases"]:
            payload = json.dumps(_load_fixture(f"{group}/{name}.json"), ensure_ascii=False)
            assert "期望" not in payload, name
            assert "expectation" not in payload, name
~~~

- [x] 运行整个 test_phase5_parallel_contract.py；预期退出 0，七个材料和四类返回原值、
  单位、时点、十三项返回及评分隔离均保持。新 _load_fixture 不交付整个 catalog 给 Agent。
- [x] 删除处置表中原 phase5_parallel 的十二份文件。新评审说明仍保留同源与独立来源、
  万元／亿元、正常／超时／越权／恶意返回和宿主无并行能力时的原限定。

## 4. 阶段验收、停止与回退

~~~bash
.venv/bin/python -m pytest -q tests/product/skill/test_work_package_contract.py tests/product/skill/test_phase5_parallel_contract.py
.venv/bin/python -m ruff check tests/product/skill/contract_fixtures.py tests/product/skill/test_work_package_contract.py tests/product/skill/test_phase5_parallel_contract.py
git diff --check
~~~

- [x] 更新路线图处置表的阶段结果；交付 5→1、12→3 的实际变化和原值恢复核对。
- [x] 停在阶段评审点，不自行启动行为 Agent、股票研究或提交。

失败时只修复本阶段消费者和生成数据；不改生产规则或原测试期望。不确定的字段原值、
身份或语义差异先报告，不能静默选择。需要回退时只恢复本阶段处置表所列文件；
从固定提交回取原件，不覆盖其他阶段或用户改动。
