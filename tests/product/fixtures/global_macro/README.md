# 全球宏观离线合成材料

## 1. 必要性与复用

现有公司、行业和中国宏观材料不覆盖全球地区差异、跨境存量与交易分解、双边相对政策及全球入口边界。本批新增独立合成原始资料 G01–G03、G05–G08，各维护一份，不把中国或公司原件改名复用。原构造器、旧材料、消费者及有效结果保持。

沿用 [material_fixtures.py](../../skill/material_fixtures.py) 的 `materialize_input(case, variant, stage, output, *, root=...)`。直接消费者是后续独立 CLI 离线研究者及独立评审者；构造器只生成输入，不执行研究，也不成为产品运行依赖。合成数字和机构不代表真实统计或新闻，定位使用块号，不伪造官方 URL。

## 2. 显式选择与上下文

| 选择 | 当次任务段 | 原始事实块 | 上下文 |
| --- | --- | --- | --- |
| GM01/main/01 | ENV、T01 | G01、G02、G03 | new |
| GM02/main/01 | ENV、T02 | G01、G03 | new |
| GM03/main/01 | ENV、T03 | G05 | new |
| GM04/main/01 | ENV、T04 | G06、G07、G08 | new |
| GM04/main/02 | T04B | 无 | continue |
| GM04/missing/01 | ENV、T04 | G07、G08 | new |
| GM05/readonly/01 | ENV、T05R | G01、G03 | new |
| GM05/save-failure/01 | ENV、T05F | G01、G03 | new |
| GM06/excluded/01 | ENV、T06E | 无 | new |
| GM06/ambiguous/01 | ENV、T06A | 无 | new |
| GM07/main/01 | ENV、T07 | G09–G12 | new |
| GM07/window/01 | ENV、T07W | G09、G11 | new |

GM01–GM06的 `as_of` 为 `2026-06-30T12:00:00+00:00`；GM07为 `2026-10-24T12:00:00+08:00`。GM04/main/02只续派原研究会话，保留原截止与前阶段输入，只增加 T04B；其余在独立新上下文运行。missing 选择不含 G06；G07 保留晚公开时间，G08 保留转载只有题名与异口径表的事实差异。GM06 两题不添加事实或代用户澄清。

GM07新增历史对照和表旁释义场景，任务与原始事实在 `inputs/history.md`。G09月表在两个选择中共用，保留真实缺值形状、独立观察末期与定义；G10仅提供季度折年增速，G11完整年度收入可比，G12保留累计金额与单次政策额度的区别。旧材料没有连续月／季／年输入，不能证明本次新增行为；原GM01–GM06十个选择及其生成字节保持。仍使用同一构造器，消费者为独立离线分析及评审；[输入测试](../../skill/test_global_macro_history_inputs.py)核新增选择的范围、缺值和口径边界，行为结果另存验证记录。

研究者仅接收当次生成的 `task.md`、`material.md`、`selection.json`，受测四文件 Skill 包及明确授权输出；原GM01–GM07没有附件；GM09的附件按选择显式供给。源 tasks/facts、catalog、本 README、设计计划、其他研究和[评审标准](review-expectations.md)留在开发／评审侧。原始事实中的外部指令只是不可信原文，不授予额外读取权限。评分与失败触发不得混入派发。

GM05/readonly 使用真实无文件写入能力环境。GM05/save-failure 的首次环境把 `delivery` 预置为内容“保留的普通文件”的普通文件，已证明正确预检查、原件保护和完整内联，但没有触发实际写失败；首次记录保留。

2026-10-24按用户授权修正该用例的执行环境：`delivery` 改为正常可检查的目录，保留同内容受保护原件；只为本次工具命令设置进程级文件长度限制，使实际保存返回真实文件系统错误。任务、事实、选择及四文件规则不变，不向研究者说明故障或提供答案。普通路径冲突不能可靠延后至保存阶段，故本次改用实际写入才触发的文件大小限制错误；不把本地环境验证等同于模型回退验证。具体本地命令、环境、原返回、单项复验及保护核对保存在[本轮记录](../../../../.hetu/validation/global-macro-fundamentals/gm05-live-completion-20261024/)。当前材料检查不认证模型行为。

## 3. 调用与验证

在仓库根使用现有 Python 环境，通过 `root` 参数指定本目录；输出必须是尚不存在的新目录：

```python
from pathlib import Path
from tests.product.skill.material_fixtures import materialize_input

materialize_input(
    "GM01", "main", "01", Path("/tmp/新的输出目录"),
    root=Path("tests/product/fixtures/global_macro").resolve(),
)
```

通用 CLI 的默认根仍指向旧材料；本批不修改它，不省略 `root`。未知选择在写入前失败，已有输出不能覆盖。输入边界、初值污染、评分路由、未知选择与覆盖保护见 [test_global_macro_inputs.py](../../skill/test_global_macro_inputs.py)。

```bash
.venv/bin/python -m pytest tests/product/skill/test_global_macro_inputs.py -q
.venv/bin/ruff check tests/product/skill/test_global_macro_inputs.py
.venv/bin/python -m pytest tests/product/skill/test_global_macro_history_inputs.py -q
git diff --check
```

红绿及 lint 实际日志保存在 [.hetu/validation/global-macro-fundamentals/](../../../../.hetu/validation/global-macro-fundamentals/)。14项输入检查只证明选择与污染边界，不证明数字真实、数学一致、宿主隔离或模型研究通过；研究输出与工具事件须另行独立评审。不重跑既有中国、个股或行业研究。

GM07的基线、两份增强后输出及增量检查见[历史对照与表旁释义验收](../../../../.hetu/validation/global-macro-history-tables-20261024/acceptance.md)。新增2项输入测试不替代实际行为评审。


## GM09：资产与美元融资反馈

新增GM09验证全景默认五项、材料受限收缩与窄专题边界。原十二选择和生成字节不变；GM08历史验证不迁移。任务与合成事实见[asset-feedback.md](inputs/asset-feedback.md)，历史块G09–G12复用原值，构造器不变。

| 选择 | 当次任务 | 原始事实 | 附件 |
| --- | --- | --- | --- |
| GM09/main/01 | ENV、T09 | G09–G20 | stablecoin-history.csv（完整25月） |
| GM09/limited/01 | ENV、T09L | G09–G16、G19–G20 | stablecoin-history-limited.csv，输出名stablecoin-history.csv（2个空值） |
| GM09/scope/01 | ENV、T09S | G15、G17 | 无 |

三项均new上下文，截止2026-10-24T12:00:00+08:00；只生成当前选择。全部新增事实为离线合成，非真实行情或新闻；源文件、catalog、评分、设计计划与其他研究不交给研究者。新[输入测试](../../skill/test_global_macro_asset_feedback_inputs.py)检查路由、缺值、污染与保护，不判分析结论。[本轮验收](../../../../.hetu/validation/global-macro-asset-feedback/acceptance.md)分别记录输入、行为和真实来源。

## GM10：取证采用与历史比较

新增GM10验证已得正文与多工作表的采用、逐指标历史比较、系列失败归属及行情窗口选择。
原15个选择、生成字节、原评分及构造器保持；问题样本原件保留。
复用[history.md](inputs/history.md)的G10季度折年增速、
[asset-feedback.md](inputs/asset-feedback.md)的G14市场与融资事实；工作簿Fiscal复制G19年度收入／净利息原值，新增支出为合成值。

| 选择 | 当次任务 | 原始事实 | 输出附件 |
| --- | --- | --- | --- |
| GM10/main/01 | ENV10、T10 | G10、G14、G21 | publisher.html、history.xlsx（完整）、request-results.json、prices.csv |
| GM10/limited/01 | ENV10、T10 | G10、G21 | publisher.html、history.xlsx（3个局部缺值）、request-results.json、prices.csv |
| GM10/scope/01 | ENV10、T10S | G14、G22 | prices.csv |

三项均new上下文，截止2026-10-25T13:55:24+08:00。任务及G21／G22在
[evidence-use.md](inputs/evidence-use.md)；合成发布页
[evidence-current.html](inputs/evidence-current.html)链接当次输出的history.xlsx。
main原件为[evidence-history.xlsx](inputs/evidence-history.xlsx)，limited原件为
[evidence-history-limited.xlsx](inputs/evidence-history-limited.xlsx)，输出名相同。
另提供[evidence-requests.json](inputs/evidence-requests.json)和
[evidence-prices.csv](inputs/evidence-prices.csv)。全部新增资料均为离线合成，不冒充真实官方网页；
G14的可比快照与SyntheticVenue成交保留不同来源、时点和用途，不能拼成同一价格序列。

直接消费者为后续独立离线研究者与独立评审者。研究者只见当次生成目录、受测四文件包及授权输出，
不能读取整个材料仓库、catalog、本说明、设计计划、旧报告或[独立评分](review-expectations.md)。
未知选择、覆盖保护、评分路由、完整工作簿污染与附件边界由
[输入测试](../../skill/test_global_macro_evidence_use_inputs.py)核验；不以输入通过代替研究行为通过。

```bash
.venv/bin/python -m pytest tests/product/skill/test_global_macro_evidence_use_inputs.py -q
.venv/bin/ruff check tests/product/skill/test_global_macro_evidence_use_inputs.py
```

开发脚本、红绿结果和旧输入保护见[本轮验证目录](../../../../.hetu/validation/global-macro-evidence-and-history/)。
