# 行业基本面离线合成材料

本组工业材料X、甲地区、机构、数字及交易均为合成，不是美国原油真实数据。现有宏观／个股材料的主体和经济含义不同，不改名复用；复用共享构造器，不修改它或旧catalog。材料直接供独立行业研究者及评审者使用，输入检查不证明模型行为、真实来源或宿主能力。

## 1. 七个显式选择

| case／variant／stage | 任务 | 事实块 | 附件 | 上下文 |
| --- | --- | --- | --- | --- |
| IF01/main/01 | ENV、MAIN | COMMON、HISTORY、STRUCTURE、COST_COMPETITION、LATE、BASE | history.csv、comparison.csv | new |
| IF01/limited/01 | ENV、MAIN | COMMON、HISTORY、STRUCTURE、COST_COMPETITION、LATE、USER | history.csv | new |
| IF01/price/01 | ENV、PRICE | COMMON、PRICE_DATA | prices.csv | new |
| IF02/entry/01 | ENV、ENTRY1 | 无 | 无 | new |
| IF02/entry/02 | ENV、ENTRY2 | 无 | 无 | continue |
| IF02/entry/03 | ENV、ENTRY3 | 无 | 无 | continue |
| IF03/circulation/01 | ENV、CIRCULATION | CIRCULATION | circulation-projects.csv、circulation-finance.csv、circulation-inventory.csv | new |

均以2026-11-01T12:00:00+08:00为截止。IF02后两轮只供给当轮task，保留原上下文，不提前提供下一轮。main／limited共享原值；limited没有comparison附件，不能读取源目录补入。LATE是故意提供的事后公告，不删掉或混成截止前事实。price只含同品报价，没有成交量／权重，也没有全景附加事实。

history.csv保留2025-01—2026-09的21个原始观察及2026-06库存空值；comparison.csv仅供main，含2024-10—12的同比基数；prices.csv复用同一报价原值，仅保留月份与价格两列。CSV定义在当次facts中，样本与总体、报价与成交、计划与有效能力分别保留。

IF03是新增的单个局部场景，复用ENV和构造器，独立原事实位于inputs/circulation-task.md、inputs/circulation-facts.md及三份circulation附件。其期间、样本和经济含义不同于IF01，不改旧原值。资料用于观察需求兑现、利润／回款／现金及财务库存判断，研究者仍只收到当前生成输入与四文件包；评审依据留在review-expectations.md。

## 2. 组装与隔离

从仓库根调用[共享构造器](../../skill/material_fixtures.py)：

```python
from pathlib import Path
from tests.product.skill.material_fixtures import materialize_input

materialize_input("IF01", "main", "01", Path("/tmp/尚未存在的行业输入目录"),
                  root=Path("tests/product/fixtures/industry_fundamentals").resolve())
```

接口为`materialize_input(case, variant, stage, output, *, root=ROOT) -> tuple[Path, ...]`。必须指定行业root，通用CLI没有`--root`。输出是task.md、material.md、selection.json及当次附件，未知选择写前失败、已有目录不覆盖。

研究者仅接收当次生成输入、受测行业四文件和授权输出位置；源catalog、本说明、[独立评审标准](review-expectations.md)、设计、计划、其他报告及后续任务不随输入提供。构造器不是操作系统沙箱，assets不会自动筛掉评分或事后信息，主持者负责选择及读取边界。

## 3. 检查入口

[输入测试](../../skill/test_industry_fundamentals_inputs.py)原14项检查六选择、截止、附件链接、原值／缺值、无覆盖、缺基数污染和评分污染负例；新增`test_circulation_selection_and_original_values`只核IF03附件、隔离及原值。完整检查命令仍为`.venv/bin/python -m pytest tests/product/skill/test_industry_fundamentals_inputs.py -q`与`.venv/bin/ruff check tests/product/skill/test_industry_fundamentals_inputs.py`；本次增量仅运行新测试节点，复用旧14项有效结果。实际模型输出、真实工具限制和来源另在本轮验收记录，不因输入通过而预判。
