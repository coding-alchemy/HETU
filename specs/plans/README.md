# HETU 执行计划索引

## 当前执行状态

本轮没有待启动计划。用户于 2026-10-02 决定不再投入性能验收，并批准清理已执行完的
执行计划。第 5–7 组限定收口及阶段 05 本批汇总与复评成果保持有效；第 8 组性能数值
未验收、五期整体未完成的事实保留，不安排新采样、配对或校准，不重复请求延期批准。

本目录只保留总索引；已执行或停止执行的旧计划已清理。五期有效要求、实现行为、
证据与未完成边界统一见[五期实现](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md)。
详细验收与预算证据归入 `specs/validation/`，现行专项结论归入五期实现，不新增或恢复执行计划。

现行事实与成果入口：

- [验收证据与批准记录](../validation/2026-10-03-phase-5-acceptance-evidence.md) §2／§6：用户批准、X1／X4 的结论与额度、本批汇总和停止投入；成本见[预算证据](../validation/2026-10-03-phase-5-budget-evidence.md) §3。
- [五期现行状态](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-status)：
  已交付行为、限定验收及未完成事实，不作为新增执行授权。

提交、推送与 main 集成按用户后续具体指令处理；清理不新增运行授权。

## 已完成功能与验收

| 范围 | 结果与现行入口 |
|---|---|
| 安装维护与旧成果 | 已交付，[实现与安全边界](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-foundation) |
| 任务控制恢复与资料复用 | 十二条功能闭环，M2-6 组合补证、历史用量对账与预算方法已通过；[行为与证据](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-middle) |
| 上下文、正式并行与三项优化 | 规则已交付，R1 开关传递问题已关闭；[行为与限制](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-execution) |
| 扩展与计量工具 | 实现、组内修复及四项阻断均已关闭；[扩展管理](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-extensions)、[计量工具](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-metering) |
| 第 5–7 组验收 | 第 5 组限定接受、第 6 组原五运行限定闭合、第 7 组本批 4/5＝80% 限定认证；[批准例外与支持边界](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-status) |
| 预算与性能 | N7 默认及获批 X1／X4 已执行，额度用尽；三条性能线、全流程取样与实测校准未完成；[停止点](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-closeout) |

## 历史与未启动范围

P2 已在 A2 资格门后停止，A3／AQ／AD 未启动；新设计和真实运行须另行批准。
既有结果按原验收范围继续有效，长期完整认证与性能未验收事实保留，不因计划清理重跑。
旧计划及原章节号从下方固定 Git 提交回溯，不重复维护分支操作过程。

V1 四期（报告信息完整性与分析有效性）已完成并由用户验收（2026-09-06，用户审阅 M04 完整报告
与 F01 降级报告后明确验收；阶段 03 行为门禁 11/11 PASS，全量门禁通过）。其执行计划已从工作区
删除；现行需求、实现设计与验收标准统一见
[V1 四期实现](../2026-09-01-stock-analysis-workflow-v1-phase-4-implementation.md)。

已完成的历史阶段（Agent 主导闭环、代码精简、独立质量加固和股票分析指南数据源分层整合，
实现提交 `7d9ce7d`、评审收尾提交 `ae74657`）计划与设计已从工作区删除，只通过 Git
历史追溯；已落地行为统一见
[个股分析工作流 V1 一期实现](../2026-08-17-stock-analysis-workflow-v1-phase-1-implementation.md)。

V1 二期加固、两轮保护性减量、来源采集与 archive 退役均已完成技术验收。相关执行计划和独立
设计均已从工作区删除，只通过 Git 历史追溯；仍有效的设计约束与最终结果统一见
[个股分析工作流 V1 二期实现](../2026-08-25-stock-analysis-workflow-v1-phase-2-implementation.md)。

V1 三期信息源研究、证据整改和 Deep 运行质量整改已经完成并由用户验收。三组执行计划和四份
独立需求/设计文档已从工作区删除，只通过 Git 历史追溯；现行能力、边界与验收结果统一见
[个股分析工作流 V1 三期实现](../2026-08-27-stock-analysis-workflow-v1-phase-3-implementation.md)，
136 条来源明细与跨阶段方法见
[A股个股分析数据源指南](../../docs/theory/A股个股分析数据源指南.md)。

个股分析工作流架构图的设计（`2026-08-26-stock-analysis-workflow-diagram-design.md`）与其执行
计划子目录已应用户要求从工作区删除，只通过 Git 历史追溯。

V1 长期需求目前只有需求，不得把需求文档当作已批准执行计划：

- [V1 长期需求：完善个股分析工作流](../stock-analysis-workflow-v1-requirements.md)

## 已清理计划的历史回溯

2026-10-02 按用户授权从工作区删除 20 份已执行完的计划文件。清理前逐文件核对，
全部与本地固定提交 `f0353f21a2acd4b6613d9ed088c12548cb3ed143` 中的字节一致。
该提交是本地历史来源，不代表服务器已同步。
验收记录、实现文档、原始 `.hetu/`、历史 check 和未完成项保留。

| 原目录（相对 `specs/plans/`） | 已清理范围 | 现行成果入口 |
|---|---|---|
| `phase5-f1-f2-migration/` | README 与阶段 01–03，共 4 份 | [扩展管理与计量工具实现](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-extensions) |
| `phase5-groups-5-7-migration/` | README 与阶段 01–03，共 4 份 | [第 5–7 组统一验收记录](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-status) |
| `zn-dev-remaining-groups-fix/` | README 与阶段 01–05，共 6 份 | [后半需求去向](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-status)、[扩展管理与计量工具实现](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-extensions) |
| `zn-dev-closeout/` | `2026-09-25-01-offline-assessment.md`、`2026-09-25-02-targeted-fixes.md`、`2026-09-25-03-quality-acceptance.md`、`2026-09-25-05-closeout.md`，共 4 份 | [验收证据](../validation/2026-10-03-phase-5-acceptance-evidence.md) §2–§5；阶段 04 及记录索引的后续清理见本节续清理记录 |
| `stock-analysis-workflow-v1-phase-5/` | `2026-09-08-03-control-context-reuse.md`、`2026-09-08-04-parallel-and-performance.md`，共 2 份 | [五期中期实现](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-middle)、[五期上下文与并行实现](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-execution) |

现行文档标为「历史」的计划链接指向本节；原章节号仍指相应固定提交中的历史文件，
不是本索引的章节号。完整文件名及原文可在仓库根目录只读查询：

```bash
git ls-tree -r --name-only f0353f21a2acd4b6613d9ed088c12548cb3ed143 -- specs/plans/
git show f0353f21a2acd4b6613d9ed088c12548cb3ed143:specs/plans/zn-dev-closeout/2026-09-25-03-quality-acceptance.md
```

首批 20 份文件使用 `f0353f21a2acd4b6613d9ed088c12548cb3ed143` 与上表原路径回源。

本次续清理剩余 7 份文件：它们已被现行实现／验收记录接替或停止执行，不继续作为工作区
计划保留。清理前逐文件确认字节完整保存在本地提交
`d12415290feb20aa117af73443251c08e1bb5e48`；本轮未创建备份或修改 Git 引用。

| 原目录（相对 `specs/plans/`） | 本次清理范围 | 现行事实入口 |
|---|---|---|
| `zn-dev-closeout/` | `2026-09-25-04-performance-calibration.md` 与 `README.md`，共 2 份 | [验收证据](../validation/2026-10-03-phase-5-acceptance-evidence.md) §2／§6／§7；原运行卡与批准全文从本地固定提交回源 |
| `stock-analysis-workflow-v1-phase-5/` | `2026-09-08-01-observation-and-evidence.md`、`2026-09-08-02-baseline-calibration.md`、`2026-09-08-06-extensions-and-archive.md`、`2026-09-08-07-host-and-final-acceptance.md` 与 `README.md`，共 5 份 | [后半需求去向](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-status)、各期实现文档及第 5–7 组统一验收记录 |

本次 7 份文件使用 `d12415290feb20aa117af73443251c08e1bb5e48` 与本次表中的原路径回源：

```bash
git show d12415290feb20aa117af73443251c08e1bb5e48:specs/plans/zn-dev-closeout/2026-09-25-04-performance-calibration.md
git show d12415290feb20aa117af73443251c08e1bb5e48:specs/plans/stock-analysis-workflow-v1-phase-5/2026-09-08-07-host-and-final-acceptance.md
```

两次累计清理 27 份计划文件；`specs/plans/` 仅保留本总索引。已接受结论、未验收事实、
原始证据和历史失败均不改写，也不因计划清理要求恢复旧批次或新增运行。

## 新增计划约定

1. 设计文档先按 `specs/YYYY-MM-DD-<topic>-design.md` 创建并获批。
2. 在本目录创建独立子目录，子目录名与设计主题一致。
3. 子目录以 `README.md` 作为路线图，链接回对应设计。
4. 分阶段文件命名为 `YYYY-MM-DD-NN-<step-title>.md`，按文件名排序即执行顺序。
5. 每阶段写明准确范围、前置条件、验证命令、停止门和回退边界；不得复用已删除的历史路径。
