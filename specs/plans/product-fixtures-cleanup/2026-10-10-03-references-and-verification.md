# 产品测试材料清理 03：引用、工程验证与交接

> 执行 Agent：使用 superpowers:executing-plans 按任务执行；步骤以复选框跟踪。
> 文档版本：v0.1
> 文档状态：已批准，执行完成，待整体验收（2026-10-11）
> 创建／修订日期：2026-10-10／2026-10-11
> 设计依据：[已批准设计](../../2026-10-10-product-fixtures-cleanup-design.md)
> 路线图及全局约束：[README](README.md)

**目标：** 新入口可用，原文件全部有去向，清理后的工程检查通过，实际净变化可复核。

**结构：** 消费前两阶段的最终材料和 API；维护现有规格入口，保留历史回源。

**技术：** Git 只读查询、Python 标准库、pytest、现有 scripts/check.sh。

## 1. 前置与文件

阶段 01、02 已通过各自评审。完整读取路线图的 136 行处置表、两阶段记录、scripts/check.sh、
以下三份现行规格涉及的材料段落；不读取股票资料，不修改历史锁定产物。

| 文件 | 修改范围 |
|---|---|
| specs/2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md | §4.1 末尾并行材料路径和十二份旧布局说明 |
| specs/2026-10-04-stock-analysis-workflow-v1-phase-6-design.md | §11.2 活跃行为输入和评审材料路径 |
| specs/2026-09-01-stock-analysis-workflow-v1-phase-4-implementation.md | 发布前核对入口，保留独立验收层 |
| tests/product/fixtures/material_cases/README.md | 当前生成输入、允许读取清单、评审入口及历史回源 |
| 本计划路线图、三个阶段文件、specs/plans/README.md | 如实更新结果和停点，不恢复历史计划 |

其余原材料按处置表保持原字节。无需修改 phase5_execution_fixture.py、来源适配器、
宿主快照、archive 或生产 Skill。出现新的直接消费者时，先回源确认再更新该引用，
将具体文件补入处置表；不能顺手重构其行为。

## 2. 任务 1：当前引用与保留材料核对

- [x] 将五期现行说明替换为以下正文；保留前后已有验收限定：

> tests/product/fixtures/parallel/ 的两个输入 catalog 保留七个汇合材料和四类返回场景。
> 转载正文只维护一份，各渠道、单位、时点和返回原值按场景恢复。
> review-expectations.md 仅供评审，不作为执行输入；历史十二文件布局从固定提交回源。

- [x] 将六期 §11.2 材料入口替换为以下正文；保留所有批次验收与未取得状态：

> 行为输入与独立评审材料位于 tests/product/fixtures/material_cases/。
> B01–B18 及增量变体继续有效，按 case／variant／stage 生成当次输入；
> 评审依据单独保存。原 phase6_materials 布局按材料 README 的固定提交回源。

- [x] 四期发布前核对仍指向 publication-review；只增加下句，不改其错误主张或有效结论：

> 当前样本的独立用途及保留理由见 tests/product/fixtures/material_cases/README.md；
> 发布前核对不由上游 owner 判断替代。

- [x] 扫描当前消费路径，逐处处理；计划处置表、材料历史说明和既有 .hetu 记录中的
  原路径是历史定位，不能为了扫描零命中改写：

~~~bash
rg -n 'phase5_parallel|phase6_materials|official_work_packages/(unregistered|uncovered|duplicate-id)' tests scripts docs specs --glob '!tests/product/fixtures/**'
~~~

- [x] 用固定基线逐字节核对处置表中“保留原字节”的文件；哈希只证明这些文件没有被本轮
  修改，不作为旧验收过期条件。原字节查询形式如下：

~~~bash
git show c9ddfaa69547d38f091e9b080f016a6f59343a7a:tests/product/fixtures/forensic/eastmoney-field-dictionary.json
~~~

同样核对 archive 四份、host_cli 五份、host_acceptance 一份、source_contracts 三份、
phase5_execution 六份和 publication-review 九份。若当前有合法后续改动，先明确归属，
不能自动用固定提交覆盖。输入字段发生差异时停止相应删除，报告最小影响。

## 3. 任务 2：最终工程门禁

- [x] 先运行直接回归：

~~~bash
.venv/bin/python -m pytest -q tests/product/skill/test_work_package_contract.py tests/product/skill/test_phase5_parallel_contract.py tests/product/skill/test_fixture_inputs.py
~~~

预期退出 0；仅因新变化或失败需要修复时重跑对应失败项，不反复加样本。

- [x] 运行正式工程检查一次，明确不加载宿主证据、不启动模型或股票研究：

~~~bash
env -u HETU_HOST_EVIDENCE -u HETU_PYTHON -u HETU_CLI bash scripts/check.sh
~~~

预期退出 0；pytest、ruff、mypy、文档、manifest 零变化、Skill 和空白检查均通过。
本计划不改 Skill，故 manifest 未变化，无需用提交绕过 manifest 门禁。
工程测试覆盖接口整合；这不等于重跑 B01–B18 的 Agent 行为验收。

如因宿主 CLI 版本漂移或其他未改环境失败，保存真实失败及归因，结束在门禁待处理点；
不自动重捕获宿主快照、不更改 skip 或放宽断言。生产规则或未取得资料仍在范围外。

- [x] 对未跟踪的新 Markdown 显式检查链接和空白，不能只依赖检查器的 tracked 范围：

~~~python
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("fixture_doc_check", "scripts/check_docs.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
documents = tuple(Path("specs/plans/product-fixtures-cleanup").glob("*.md")) + (
    Path("specs/2026-10-10-product-fixtures-cleanup-design.md"),
) + tuple(Path("tests/product/fixtures/material_cases").rglob("*.md")) + (
    Path("tests/product/fixtures/parallel/review-expectations.md"),
)
assert not module._link_failures(Path.cwd(), documents)
for path in documents:
    text = path.read_text(encoding="utf-8")
    assert all(line == line.rstrip() for line in text.splitlines()), path
    assert text.endswith("\n") and not text.endswith("\n\n"), path
~~~

## 4. 任务 3：净变化与交接

- [x] 对固定基线和工作区采用一致口径统计：fixture 文件、UTF-8 行、字节，PDF 单列；
  再统计新增 helper、测试、配置、设计与计划，给出全仓净变化。
  JSON 中存储的重复正文是否消除用字段恢复核对说明，不把合并文件或改换行算成内容删除。
  在仓库根执行下段只读统计，保存输出到本轮交付记录：

~~~python
import json
import subprocess
from pathlib import Path

baseline = "c9ddfaa69547d38f091e9b080f016a6f59343a7a"


def git_names(*args):
    raw = subprocess.check_output(["git", *args])
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


old = {
    name: subprocess.check_output(["git", "show", f"{baseline}:{name}"])
    for name in git_names("ls-tree", "-r", "--name-only", "-z", baseline)
}
new = {
    name: Path(name).read_bytes()
    for name in git_names("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if Path(name).is_file()
}


def count(blobs):
    result = {key: 0 for key in (
        "files", "bytes", "utf8_lines", "pdf_files", "pdf_bytes", "json_formatted_lines"
    )}
    for name, raw in blobs.items():
        result["files"] += 1
        result["bytes"] += len(raw)
        if name.endswith(".pdf"):
            result["pdf_files"] += 1
            result["pdf_bytes"] += len(raw)
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        result["utf8_lines"] += len(text.splitlines())
        if name.endswith(".json"):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            result["json_formatted_lines"] += len(
                json.dumps(value, ensure_ascii=False, indent=2).splitlines()
            )
    return result


def group(name):
    if name.startswith("tests/product/fixtures/"):
        return "fixtures"
    if name.startswith("tests/"):
        return "helpers_and_tests"
    if name.startswith(("specs/", "docs/")):
        return "documents"
    return "other"


for bucket in ("fixtures", "helpers_and_tests", "documents", "other", "whole_repository"):
    a = count({n: b for n, b in old.items() if bucket == "whole_repository" or group(n) == bucket})
    b = count({n: v for n, v in new.items() if bucket == "whole_repository" or group(n) == bucket})
    print(json.dumps({"scope": bucket, "baseline": a, "current": b,
                      "net_change": {key: b[key] - a[key] for key in a}}, ensure_ascii=False))
~~~

预期 fixtures 基线为 136／4010／266889，三 PDF；布局推算当前约 76 文件。
其余结果取实际值，不设置必须净减多少行的断言。json_formatted_lines 仅用于识别换行
导致的表观变化，原字段恢复和逐字共用段才是内容去重证据；全仓统计不含 gitignore 内产物。
- [x] 检查处置表 136/136，删除条目均有新消费者或历史回源；列明实际共用内容、
  仍有独立用途的保留项及预计 35–50 文件／400–650 行与实际结果的差异。
- [x] 更新设计的交付状态、路线图及总索引，保留原基线数值供比较；不冒称资料缺口关闭。
- [x] 交付修改／新增／删除范围、直接回归、正式门禁退出码、工作区与暂存区状态，
  无关改动单列。停在整体验收点，提交／推送／合入 main 未授权。

仅路径和输入组织变化时复用有效 Agent 行为证据；任何具体语义差异先报告并取得批准，
按 AGENTS.md 确定最小重验。阶段 03 失败不追溯使阶段 01、02 或历期成果失效。
回退限本阶段文档和直接消费者，不能整体恢复旧分支或删除历史证据。
