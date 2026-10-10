# 产品测试材料清理 02：场景归并与输入隔离

> 执行 Agent：使用 superpowers:executing-plans 按任务执行；步骤以复选框跟踪。
> 文档版本：v0.1
> 文档状态：已完成，复评通过（2026-10-11）
> 创建／修订日期：2026-10-10／2026-10-11
> 设计依据：[已批准设计](../../2026-10-10-product-fixtures-cleanup-design.md)
> 路线图及全局约束：[README](README.md)

**目标：** B01–B18 及增量变体仍可独立选择，共同说明只维护一份，当次输入不泄露未来阶段和答案。

**结构：** 原文存入按用途分组的 Markdown；catalog 只列明每个选择所需的原文段和附件。
materialize_input 只复制当次输入，不解释内容、不做变量替换、条件执行或 Agent 调度。

**技术：** Python 标准库、pytest、Markdown 原文、静态 JSON 定位表。

## 1. 前置、文件与接口

阶段 01 已通过；本阶段完整读取原 phase6_materials 的 90 份材料及 publication-review 九份材料，
包括三个 PDF 的原字节和全部评审标准。逐文件去向在路线图 §6；不能只读本计划中的示例。

| 文件 | 责任 |
|---|---|
| tests/product/fixtures/material_cases/inputs/各用途/B01.md–B18.md | 存放该 case 的独有原文段，内部块不直接交付 Agent |
| tests/product/fixtures/material_cases/shared.md | 仅保存逐字相同的共享段 |
| tests/product/fixtures/material_cases/catalog.json | 精确 case／variant／stage → 当次任务、材料段、附件和上下文条件 |
| tests/product/fixtures/material_cases/assets/B13/market-cap.json | 原计算输入；仅 main/03 阶段提供 |
| tests/product/fixtures/material_cases/assets/B15/三个 PDF | 原字节移动；仅 main/01 阶段提供 |
| tests/product/fixtures/material_cases/review-expectations/B01.md–B18.md | 独立评分；保留每项标准和失败触发 |
| tests/product/fixtures/material_cases/README.md | 当前调用、白名单、评审入口、原件回源和保留理由 |
| tests/product/skill/material_fixtures.py | 小型输入生成 helper 及同模块离线命令入口 |
| tests/product/skill/test_fixture_inputs.py | 当次给料、时点、变体、附件和答案隔离验证 |
| 原 phase6_materials 90 份 | 替代验证后删除；歧义初版只历史回源 |
| tests/product/fixtures/publication-review 九份 | 原字节保留；当前内容与其他 case 并非同一原件 |

**产生接口：**
materialize_input(case: str, variant: str, stage: str, output: Path, *, root: Path = ROOT)
-> tuple[Path, ...]，返回唯一允许提供给执行者的文件清单；root 仅供测试选择语料副本。
调用方不把 catalog、输入源目录或评审文件加入执行者可读清单。
前一阶段历史输出由既有上下文规则处理，不作为新增源数据自动载入。

## 2. 任务 1：先验证输入边界

- [x] 新建 test_fixture_inputs.py，内容如下。新 helper 尚不存在时运行该文件，
  预期收集 FAIL；不能编写只断言生成器自己的实现细节的镜像测试。

~~~python
import json
import shutil
from pathlib import Path

import pytest
from tests.product.skill.material_fixtures import ROOT, materialize_input


def _text(paths: tuple[Path, ...]) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths if p.suffix == ".md")


def _stage_two_boundary(paths: tuple[Path, ...]) -> None:
    text = _text(paths)
    assert "营业收入 100" in text or "营业收入 100／" in text
    assert "分部收入 A=60" not in text
    assert "一次性收益 3" not in text


def test_partial_material_never_contains_later_notes(tmp_path: Path) -> None:
    _stage_two_boundary(materialize_input("B16", "main", "02", tmp_path / "s2"))
    text = _text(materialize_input("B16", "main", "03", tmp_path / "s3"))
    assert "分部收入 A=60" in text
    assert "一次性收益 3" in text


@pytest.mark.parametrize("variant", ["closed", "industry-closed"])
def test_isolated_reuse_off_has_no_old_values(tmp_path: Path, variant: str) -> None:
    text = _text(materialize_input("B17", variant, "01", tmp_path / variant))
    assert "reuse_previous_task_data=false" in text
    for forbidden in ("T1-root", "P1", "W-A-root", "100", "10.2"):
        assert forbidden not in text


def test_deliberate_old_clue_remains_input_not_a_file_to_read(tmp_path: Path) -> None:
    paths = materialize_input("B17", "integration-off", "01", tmp_path / "old-clue")
    text = _text(paths)
    assert "10.2" in text and ".hetu/research/jia-20260331/" in text
    assert {p.name for p in paths} == {"task.md", "material.md", "selection.json"}


def test_copy_variant_keeps_the_permitted_original_facts(tmp_path: Path) -> None:
    text = _text(materialize_input("B17", "copy", "01", tmp_path / "copy"))
    assert "营业收入 100、净利润 10、经营活动现金流量净额 12" in text
    assert "2026-04-20 19:00+08:00" in text
    assert "T1 的旧目录 T1-root 不在 T2 的允许输入集" in text
    assert "Q3-R01" not in text and "复用开关 false" not in text


def test_new_time_and_dual_sections_are_preserved(tmp_path: Path) -> None:
    paths = materialize_input("B11", "main", "02", tmp_path / "b11")
    selection = json.loads((tmp_path / "b11/selection.json").read_text())
    assert selection["context"] == "independent"
    assert selection["as_of"] == ["2026-07-02T12:00:00+08:00"]
    assert "第 407 条" in _text(paths)
    text = _text(materialize_input("B14", "main", "01", tmp_path / "b14"))
    assert "2026-03-31T12:00:00+08:00" in text
    assert "2026-06-30T12:00:00+08:00" in text


def test_only_current_assets_are_copied(tmp_path: Path) -> None:
    early = materialize_input("B13", "main", "01", tmp_path / "early")
    assert "market-cap.json" not in {p.name for p in early}
    late = materialize_input("B13", "main", "03", tmp_path / "late")
    assert "market-cap.json" in {p.name for p in late}
    pdfs = materialize_input("B15", "main", "01", tmp_path / "pdf")
    for name in ("corrupt.pdf", "encrypted.pdf", "no-text.pdf"):
        target = next(p for p in pdfs if p.name == name)
        assert target.read_bytes() == (ROOT / "assets/B15" / name).read_bytes()
        assert not target.is_symlink()


@pytest.mark.parametrize(
    "case,variant,stage", [("B99", "main", "01"), ("B16", "missing", "01"), ("B16", "main", "99")]
)
def test_unknown_selection_creates_nothing(
    tmp_path: Path, case: str, variant: str, stage: str
) -> None:
    output = tmp_path / "unknown"
    with pytest.raises(KeyError):
        materialize_input(case, variant, stage, output)
    assert not output.exists()


def test_contaminated_route_fails_the_boundary_check(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    data = json.loads(path.read_text())
    data["B16/main/02"]["material"] += data["B16/main/03"]["material"]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        _stage_two_boundary(
            materialize_input("B16", "main", "02", tmp_path / "bad", root=root)
        )


def test_review_file_cannot_be_routed_into_an_input(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    shutil.copytree(ROOT, root)
    path = root / "catalog.json"
    data = json.loads(path.read_text())
    data["B16/main/02"]["task"] = [["review-expectations/B16.md", "B16-001"]]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "review-leak"
    with pytest.raises(KeyError):
        materialize_input("B16", "main", "02", output, root=root)
    assert not output.exists()


def test_reused_task_and_originals_have_one_maintained_copy() -> None:
    data = json.loads((ROOT / "catalog.json").read_text())
    first = data["B16/main/01"]["task"]
    assert first == data["B16/main/02"]["task"] == data["B16/main/03"]["task"]
    bodies = []
    for path in [ROOT / "shared.md", *sorted((ROOT / "inputs").rglob("*.md"))]:
        for block in path.read_text(encoding="utf-8").split("<!-- fixture:")[1:]:
            bodies.append(block.split("-->\n", 1)[1].strip())
    assert len(bodies) == len(set(bodies))


def test_every_registered_selection_is_individually_materializable(tmp_path: Path) -> None:
    data = json.loads((ROOT / "catalog.json").read_text())
    assert {key.split("/")[0] for key in data} == {f"B{i:02d}" for i in range(1, 19)}
    assert len(data) == 48
    for key in data:
        case, variant, stage = key.split("/")
        paths = materialize_input(case, variant, stage, tmp_path / key.replace("/", "-"))
        assert len(paths) == len(set(paths))
        assert all(p.parent == paths[0].parent for p in paths)
        assert not any("review" in p.name or p.name == "catalog.json" for p in paths)
        assert "独立判定依据" not in _text(paths)
~~~

~~~bash
.venv/bin/python -m pytest -q tests/product/skill/test_fixture_inputs.py
~~~

这里的负例检查实际生成文件：把后续分部附注误加进第二阶段时，边界检查必须失败。
它不要求模型重做业务判断，也不以 helper 输出“pass”字符串自证隔离。

## 3. 任务 2：建立共享原文和明确选择

- [x] 逐项核对原文件与路线图 §6；原件从固定提交可回取，当前有差异则先明确归属。
  原文不做同义改写。已发现共享段为合成声明、共同任务边界、相同 standard 截止说明、
  相同原件标题、主体说明及同一官方融券补充段；其他相似内容不能为了复用修改事实。
- [x] 用以下一次性代码转换原输入。catalog 是数据定位表，Markdown 注释仅标识存储段，
  不构成模板语言；没有表达式、条件计算、网络请求或脚本执行。

~~~python
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

old = Path("tests/product/fixtures/phase6_materials")
new = Path("tests/product/fixtures/material_cases")
new.mkdir(parents=True, exist_ok=False)
domains = {
    **{f"B{i:02d}": "coverage" for i in range(1, 5)},
    "B05": "company", "B06": "company",
    "B07": "business", "B08": "business", "B09": "business",
    "B10": "risk", "B11": "risk", "B12": "risk",
    "B13": "market", "B14": "market", "B15": "acquisition",
    "B16": "lifecycle", "B17": "lifecycle", "B18": "safety",
}
source = {
    p.relative_to(old / "inputs").as_posix(): p.read_text(encoding="utf-8").strip()
    for p in sorted((old / "inputs").rglob("*.md"))
    if p.name != "integration-variant-a.md"
}
counts = Counter(part for text in source.values() for part in text.split("\n\n"))
shared = {text: f"S{i:03d}" for i, text in enumerate(
    (text for text, count in counts.items() if count > 1), 1
)}
page_fact = next(line for line in source["B17/stage-01.md"].splitlines()
                 if line.startswith("- T1 已取得："))
shared[page_fact] = f"S{len(shared) + 1:03d}"
blocks = defaultdict(list)
for text, key in shared.items():
    blocks["shared.md"].append((key, text))
numbers = Counter()
used = set()
unique_refs = {}


def parts(case, text):
    result, pending = [], []
    relative = f"inputs/{domains[case]}/{case}.md"

    def flush():
        if pending:
            body = "\n\n".join(pending)
            cached = (case, body)
            if cached not in unique_refs:
                numbers[case] += 1
                key = f"{case}-{numbers[case]:03d}"
                blocks[relative].append((key, body))
                unique_refs[cached] = [relative, key]
            result.append(unique_refs[cached])
            pending.clear()

    for paragraph in text.strip().split("\n\n"):
        if paragraph in shared:
            flush()
            result.append(["shared.md", shared[paragraph]])
        else:
            pending.append(paragraph)
    flush()
    return result


def read(relative):
    used.add(relative)
    return source[relative]


catalog = {}
expected = {}
default_asof = ["2026-06-30T12:00:00+08:00"]


def register(case, variant, stage, task, material, assets=(), context=None, asof=None):
    selector = f"{case}/{variant}/{stage}"
    expected[selector] = (task.strip(), material.strip())
    catalog[selector] = {
        "task": parts(case, task) if task else [],
        "material": parts(case, material) if material else [],
        "assets": [list(pair) for pair in assets],
        "context": context or ("new" if stage == "01" else "continue"),
        "as_of": asof or default_asof,
    }


stages = {
    "B01": ("01", "02"), "B02": ("01", "02", "03"), "B03": ("01", "02"),
    "B04": ("01",), "B05": ("01", "02"), "B06": ("01",),
    "B07": ("01", "02"), "B08": ("01", "02"), "B09": ("01", "02"),
    "B10": ("01", "02"), "B11": ("01", "02"), "B12": ("01", "02"),
    "B13": ("01", "02", "03"), "B14": ("01", "02"), "B15": ("01", "02"),
    "B16": ("01", "02", "03"), "B18": ("01", "02"),
}
for case, sequence in stages.items():
    for stage in sequence:
        task = read(f"{case}/task.md")
        material = read(f"{case}/stage-{stage}.md")
        assets, context, asof = [], None, None
        if case == "B11" and stage == "02":
            task = task.replace("as_of=2026-06-30T12:00:00+08:00",
                                "as_of=2026-07-02T12:00:00+08:00")
            material = read("B11/stage-01.md") + "\n\n" + material
            context, asof = "independent", ["2026-07-02T12:00:00+08:00"]
        if case == "B14":
            asof = ["2026-03-31T12:00:00+08:00", "2026-06-30T12:00:00+08:00"]
        if case == "B13" and stage == "03":
            assets = [("assets/B13/market-cap.json", "market-cap.json")]
        if case == "B15" and stage == "01":
            assets = [(f"assets/B15/{name}", name)
                      for name in ("corrupt.pdf", "encrypted.pdf", "no-text.pdf")]
        register(case, "main", stage, task, material, assets, context, asof)
for case in ("B16", "B18"):
    for stage in ("01", "02"):
        register(case, "integration", stage, read(f"{case}/integration-task.md"),
                 read(f"{case}/integration-stage-{stage}.md"))

text = read("B17/stage-01.md")
first = "## 变体一：任务 T1（当前任务恢复）"
second = "## 变体二：任务 T2（独立重试／新时点）"
third = "## 变体三：任务 T3（复用开关 false）"
assert all(marker in text for marker in (first, second, third))
restore = first + text.split(first, 1)[1].split(second, 1)[0].rstrip()
copied = second + text.split(second, 1)[1].split(third, 1)[0].rstrip()
before, after = restore.split(page_fact, 1)
restore = "\n\n".join((before.rstrip(), page_fact, after.lstrip()))
copied += "\n\n" + page_fact
task = read("B17/task.md")
register("B17", "restore", "01", task, restore)
register("B17", "restore", "02", task, read("B17/stage-02.md"))
register("B17", "copy", "01", task.replace(
    "as_of=2026-06-30T12:00:00+08:00", "as_of=2026-07-01T12:00:00+08:00"
), copied, asof=["2026-07-01T12:00:00+08:00"])
register("B17", "closed", "01", read("B17/variant-3-task.md"), "")
boundary = "只使用本次提供的资料和当前有效规则，不联网、不寻找其他公司资料。" \
           "只写本次获准输出目录。完成后交付可打开的判断结果和依据。"
industry_task = boundary + "\n\n本次问题用途归属 W3（行业价格库存产能）；" \
                "请按所读规则形成必需项表相关行、证据定位、影响和回访条件。"
register("B17", "industry", "01", industry_task, read("B17/stage-03.md"))
register("B17", "industry", "02", industry_task, read("B17/stage-04.md"))
register("B17", "industry-closed", "01", read("B17/variant-w3-closed-task.md"), "")
register("B17", "integration-on", "01", read("B17/integration-variant-a2.md"), "")
register("B17", "integration-off", "01", read("B17/integration-variant-b.md"), "")
assert used == set(source), sorted(set(source) - used)
assert len(catalog) == 48
assert all(len({body for _, body in items}) == len(items) for items in blocks.values())
for selector, entry in catalog.items():
    actual = tuple("\n\n".join(
        next(body for block_key, body in blocks[relative] if block_key == key)
        for relative, key in entry[kind]
    ).strip() for kind in ("task", "material"))
    assert actual == expected[selector], selector

for relative, items in blocks.items():
    path = new / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(f"<!-- fixture:{key} -->\n{body}" for key, body in items)
                    + "\n", encoding="utf-8")
(new / "catalog.json").write_text(
    "{\n" + ",\n".join(
        f"  {json.dumps(key)}: {json.dumps(entry, ensure_ascii=False)}"
        for key, entry in catalog.items()
    ) + "\n}\n", encoding="utf-8"
)
for relative in ("B13/market-cap.json", "B15/corrupt.pdf", "B15/encrypted.pdf", "B15/no-text.pdf"):
    target = new / "assets" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(old / "inputs" / relative, target)
    assert target.read_bytes() == (old / "inputs" / relative).read_bytes()
shutil.copytree(old / "review-expectations", new / "review-expectations")
~~~

任务二 B11 使用独立的新时点，但仍需要第一阶段原始实体、所有权规则和查询词：
提供原始材料，不载入第一阶段判断。任务截止显式改成原已批准的 7 月 2 日；
B17 copy 同样显式使用原 T2 的 7 月 1 日，原取得时间、发布时间和旧任务结果均不改。
上述是原选择条件的明确化，不能据此宣称历史初派具备本轮新的输入组织。
catalog 每个选择占一行便于单独维护；这种换行方式不登记为实质内容减量。

- [x] 核对所有生成任务／材料与对应原件：除共同段存储、选择标签和上述已存在的新时点参数外，
  原事实、主体、角色、期限、同源关系、数值和限制逐字保持。B17 main 复合材料按原段落拆给
  restore／copy；closed 和 industry-closed 必须只使用独立材料，不使用复合 stage-01。
  T2 仍需原许可复制页的 100／10／12 与原取得时间，所以与 T1 共用该条原始取得记录；
  不把 T1 的任务目录权限或 T3 的失败条件交给 T2。为定位该原文句而增加段落边界，文字原值不变。
  分享原文不改成“分享判断”，不归并不同债券、法域、产品或证券类别。
  同一任务复用于多阶段时引用同一段，不再存储任务全文副本；六类共同段和上述 B17 原始记录只存一份。

## 4. 任务 3：实现当次输入生成

- [x] 新建 material_fixtures.py，完整实现如下；不增加缓存、全局运行状态或扩展协议：

~~~python
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures/material_cases"


def _render(root: Path, references: list[list[str]]) -> str:
    parts = []
    for relative, key in references:
        if relative != "shared.md" and not relative.startswith("inputs/"):
            raise KeyError(relative)
        text = (root / relative).read_text(encoding="utf-8")
        marker = f"<!-- fixture:{key} -->\n"
        if text.count(marker) != 1:
            raise KeyError((relative, key))
        parts.append(text.split(marker, 1)[1].split("\n<!-- fixture:", 1)[0].strip())
    return "\n\n".join(parts) + ("\n" if parts else "")


def materialize_input(
    case: str,
    variant: str,
    stage: str,
    output: Path,
    *,
    root: Path = ROOT,
) -> tuple[Path, ...]:
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    key = f"{case}/{variant}/{stage}"
    entry = catalog[key]
    task = _render(root, entry["task"])
    material = _render(root, entry["material"])
    selection = {
        "case": case, "variant": variant, "stage": stage,
        "context": entry["context"], "as_of": entry["as_of"],
    }
    output.mkdir(parents=True, exist_ok=False)
    paths = []
    for name, text in (("task.md", task), ("material.md", material)):
        path = output / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    selected = output / "selection.json"
    selected.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    paths.append(selected)
    for relative, name in entry["assets"]:
        target = output / name
        shutil.copyfile(root / relative, target)
        paths.append(target)
    return tuple(paths)


def main() -> None:
    parser = argparse.ArgumentParser(description="仅生成指定合成场景的当前输入")
    for name in ("case", "variant", "stage", "output"):
        parser.add_argument(name)
    args = parser.parse_args()
    for path in materialize_input(args.case, args.variant, args.stage, Path(args.output)):
        print(path.resolve())


if __name__ == "__main__":
    main()
~~~

- [x] 运行 test_fixture_inputs.py；预期退出 0。检查 48 个选择分别可生成，
  B16 后续值污染负例被发现，B17 两类关闭输入各自保持，三个 PDF 原字节复制，
  B13 计算文件仅在明确提供它的第三阶段出现。
- [x] 在删除前，把全部注册选择生成到新临时目录，人工核对任务条件与全部 case 标准；
  不启动 Agent。不得仅用“文件存在”或解析器成功替代语义核对。
  转换时的 48 项原文恢复断言与测试的输入污染反例分别成立，二者不能互相替代。

## 5. 任务 4：评审入口与历史分离

- [x] 逐份修改移入的 18 份评审依据：只删除纯路径、代理、费用和派发过程说明，
  当前指针改为 catalog 选择和本目录；每项判定标准、失败触发和有效限定保留。
  “证据位置”中的通过条件是标准，不随路径整段删除；B16–B18 后续增量节也不能裁掉。
- [x] B17 整合变体 A 当前输入登记为 B17/integration-on/01（a2），初版不再用作活跃评审输入；
  三要素中复制时间未证明的历史限定继续有效，不把重新组织当成补造执行证据。
- [x] 编写 material_cases/README.md，至少写入以下准确入口和规则：

~~~bash
.venv/bin/python -m tests.product.skill.material_fixtures B16 main 02 .hetu/validation/fixture-cleanup/inputs/B16-main-02
~~~

生成路径必须是不存在的新目录。返回的 task.md、material.md、selection.json 和当次附件
是全部可读输入；不把源 bundle、catalog、shared.md、其他变体或 review-expectations
加入执行者清单。读取 canonical 规则的既有权限保持，helper 不推断模型或替代 owner 判断。
context=new／continue／independent 说明沿用原上下文、续派或独立新时点要求，不自动派发。

原件回源统一使用 c9ddfaa69547d38f091e9b080f016a6f59343a7a 与路线图 §6 的原路径；
该提交只记录历史来源，不代表远端同步或触发过期。说明 publication-review 九份与
phase5_execution 六份有独立用途，保留原字节，不强行参数化 14 行输入。

- [x] 当前输入、任务范围及评分映射核对通过后，按处置表删除原 phase6_materials 的 90 份文件。
  原 .hetu 派发、输出、评审和锁定产物不改；没有被 pytest 读取不等于无用。

## 6. 阶段验收、停止与回退

~~~bash
.venv/bin/python -m pytest -q tests/product/skill/test_fixture_inputs.py
.venv/bin/python -m ruff check tests/product/skill/material_fixtures.py tests/product/skill/test_fixture_inputs.py
git diff --check
~~~

- [x] 核对 fixture PDF／计算输入移动前后原字节一致，所有原 Markdown 均有消费映射，
  歧义初版有固定提交回源；新增文件同样检查末尾与空白。
- [x] 交付当前 48 个选择、18 个 case、变体／时点／附件清单和原文等义核对；
  暂不声称新增一次业务验收，也不声明宿主安全认证。
- [x] 停在阶段评审点，未授权进入 Agent 行为试跑或股票研究。

如发现任何未批准的角色、时点、资料范围、判断标准变化，停止该场景删除，先报告直接影响；
不能靠修改期望值使其通过。重验遵守设计及 AGENTS.md，仅具体语义变化时申请最小范围。
回退仅恢复本阶段 fixture、helper 和测试；不覆盖阶段 01、用户改动或历史锁。
