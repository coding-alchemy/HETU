# HETU 执行计划索引

## 当前状态

2026-10-17 按用户“同等效果已经确认，只缺实际输入差额及归因”的最新指令完成存量账目核对，
本次准备交付并清理计划。当前没有待执行计划；旧补件、筛样本和 G2 路径不再作为本次核账前提。

Standard Token 优化的 M1–M5 已落地，现行行为与兼容边界见
[优化设计](../2026-10-15-standard-token-efficiency-design.md)及其链接的 canonical references。
[原执行记录](../../.hetu/validation/standard-token-efficiency/report.md)、
[追加验证](../../.hetu/validation/standard-token-efficiency-appendix-20261015/report.md)保留各自条件下的结论：
E2 局部效果成立、经济性未证；Codex 外层截断及网络即时保存局部成立；默认模型／上下文策略未切换。
原全量工程记录为 1825 passed／6 skipped／2 failed，两项失败已定位为提交状态相关；不因清理重跑。

本轮[实际输入差额及环节归因](../../.hetu/validation/standard-token-comparison-20261015/input-delta-attribution-20261017.md)
给出两份指定作者记录 **29,871,095 → 24,183,238，减少 5,687,857（19.04%）**，四组环节差额可逐行平账。
两侧修订截点不同，该数值是实际记录差额；完整日常交付总成本及 M1–M5 各自纯因果贡献尚未精确识别，
不将其写成全部由机械优化带来的节约。效果不再重验，历史材料缺口仍按原范围保留；G2从未启动。
七份已执行或被最新方向替代的计划已清理，原文及状态去向见[清理记录](#standard-token-efficiency-closeout)。

七期“分层优化与 Quick／Standard 能力补全”已按最新批准范围验收完成；
2026-10-14 按用户授权清理本批路线图及三份阶段计划，原文回源见[清理记录](#standard-scope-closeout)。

三档分级与后续工作流/Quick/Standard补全现统一归为已完成的七期；需求、设计、实现、
能力及验收边界只由[七期实现：分层优化与能力补全](../2026-10-12-stock-analysis-workflow-v1-phase-7-implementation.md)维护。
Q01–Q08／S01–S08及适用验证已通过，Deep仅整理；S04采用用户2026-10-14修正后的正式披露核对范围。

两次执行的历史批准、计划与证据分别保留，不维护两套现行规格。原三档6份文档见
[旧回源归档](../../.hetu/validation/depth-tiers/closeout-v0.3-20261012/README.md)，
后续需求/设计与合并前规范见[本次合并回源](../../.hetu/validation/standard-scope-adjustment/consolidation-20261014/README.md)。
后续四份计划的清理回源仍见[历史记录](#standard-scope-closeout)。归档不是待执行计划，不触发研究重跑。

产品测试材料清理三阶段已执行并评审通过，执行计划已按授权清理。
后续维护遵循[测试材料复用与维护方法论](../../docs/engineering/test-fixtures-methodology.md)。

六期已按用户批准的已证范围限定验收，正式全仓工程门禁与收尾交接已完成；
2026-10-10 按用户授权删除六期路线图及六份编号计划，原文从本页的固定 Git 提交回溯。

第 02 批限定接受、第 04／05／06 批后置决定及全部使用限制继续有效。未取得资料保留在
[长期需求资料清单](../stock-analysis-workflow-v1-requirements.md#2-资料取得与来源工程)，
清理不将取得目标改判完成、不启动新的实现或研究；推送／合入 main 未授权未执行。

用户于 2026-10-02 决定不再投入五期性能验收，并批准清理已执行完的计划。第 5–7 组限定
收口及阶段 05 本批汇总与复评成果保持有效；第 8 组性能数值未验收、五期整体未完成的事实
保留，不安排新采样、配对或校准，不重复请求延期批准。

已执行或停止执行的旧计划已清理；当前没有待执行计划。五期有效要求、实现行为、
证据与未完成边界统一见[五期实现](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md)。
详细验收与预算证据归入 `specs/validation/`，现行专项结论归入五期实现，不恢复五期旧计划。

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
| 六期资料完整性与证据闭合 | 已证范围获限定验收、工程收尾完成；[已交付范围与证据入口](../2026-10-04-stock-analysis-workflow-v1-phase-6-implementation.md#6-交付与验收结果)，剩余取得要求仍在长期清单 |
| 七期分层优化与能力补全 | Q01–Q08／S01–S08及完整Standard交付按批准范围验收；[七期实现](../2026-10-12-stock-analysis-workflow-v1-phase-7-implementation.md#13-当前完成边界)维护需求、设计、实现与证据，Deep仅整理 |
| Standard Token 机械优化与核账 | [实现范围与规则](../2026-10-15-standard-token-efficiency-design.md)、[局部验证](../../.hetu/validation/standard-token-efficiency-appendix-20261015/report.md)及[实际差额归因](../../.hetu/validation/standard-token-comparison-20261015/input-delta-attribution-20261017.md)；默认策略未切换，金额与分项纯因果贡献未证 |

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

2026-10-02 两次累计清理 27 份计划文件；清理当时 `specs/plans/` 仅保留本总索引。
已接受结论、未验收事实、原始证据和历史失败均不改写，也不因计划清理恢复旧批次或新增运行。

2026-10-10 六期收尾后，按用户授权删除 `stock-analysis-workflow-v1-phase-6/` 中的路线图
及 01–06 六份编号计划，共 7 份。清理前逐文件确认原字节完整保存在本地固定提交
`4723a27dd7daf6fa45d4c4763da53bf8fd10986d`。本轮清理只移除已结束的执行安排、旧报批、
提交示例及重复记录；未勾选的取得目标仍按实际未满足状态转长期，不登记为取得通过。

六期现行行为、用途与验收范围统一见[六期实现](../2026-10-04-stock-analysis-workflow-v1-phase-6-implementation.md)；
后置要求仍在长期清单。各批验收、原件、失败、修订及锁定产物保留；验收记录中的原计划路径
和章节号指清理前版本，可按以下方式回源，不作为工作区待执行入口：

```bash
git ls-tree -r --name-only 4723a27dd7daf6fa45d4c4763da53bf8fd10986d -- specs/plans/stock-analysis-workflow-v1-phase-6/
git show 4723a27dd7daf6fa45d4c4763da53bf8fd10986d:specs/plans/stock-analysis-workflow-v1-phase-6/README.md
```

2026-10-11 按用户授权删除 `product-fixtures-cleanup/` 中的路线图及 01–03 三份编号计划，
共 4 份。删除前逐文件确认原字节保存在本地固定提交
`c1ed5042bf7c65620fdc34c0a831fe88ca9017c8`；该提交也保存了 136 项处置表及三阶段执行记录。
当前材料入口见[合成场景材料](../../tests/product/fixtures/material_cases/README.md)，
后续维护遵循[测试材料复用与维护方法论](../../docs/engineering/test-fixtures-methodology.md)。
原设计及交付结果保存在 `b5a23c8ac6803a76225811ddbc897d5ff5deb124`，原文按以下路径回源。

```bash
git show c1ed5042bf7c65620fdc34c0a831fe88ca9017c8:specs/plans/product-fixtures-cleanup/README.md
git show b5a23c8ac6803a76225811ddbc897d5ff5deb124:specs/2026-10-10-product-fixtures-cleanup-design.md
```

<a id="standard-scope-closeout"></a>

### Quick／Standard 能力补全计划清理

2026-10-14 按用户授权删除 `standard-scope-adjustment/` 的 `README.md` 及
`2026-10-13-01-rules-and-contracts.md`、`2026-10-13-02-isolated-materials.md`、
`2026-10-13-03-behavior-and-closeout.md`，共四份。删除前逐份确认完成状态，并将未提交原字节
保存到[可恢复归档](../../.hetu/validation/standard-scope-adjustment/closeout-20261014/README.md)，
逐份 SHA-256 与归档解压内容一致。归档仅作历史回源，不作为待执行计划。

当前结果见[总验收报告](../../.hetu/validation/standard-scope-adjustment/report.md)及
[修订四独立锁后评分](../../.hetu/validation/standard-scope-adjustment/standard-review/revisions/revision-04/scoring.md)；
原验收记录中的计划路径和章节号指清理前版本，可从归档回源。研究树、锁、旧失败、
预算及限定接受记录保留，不因本次清理失效或重跑。

<a id="standard-token-efficiency-closeout"></a>

### Standard Token 优化计划清理

2026-10-17 按用户交付清理指令，删除 `standard-token-efficiency/README.md` 及同目录的六份
`2026-10-15-01` 至 `2026-10-15-06` 编号计划，共七份。未提交原字节在删除前保存为
[可恢复归档](../../.hetu/validation/standard-token-efficiency/closeout-20261017/README.md)，
逐份大小、SHA-256 与归档解压内容核对一致；完整文件名见归档清单。

阶段 01–03 的实现及局部验证、04 的离线审计继续有效；05 的已授权材料尝试按实际结果结束，
G2从未启动，未执行项不记通过。用户最新确认同等效果后，06 的核账结果由
[实际输入差额及环节归因](../../.hetu/validation/standard-token-comparison-20261015/input-delta-attribution-20261017.md)
承接，保留计量边界及纯因果贡献未识别的限制。旧补件／配对安排不再作为当前执行入口。

原记录中的计划路径和章节号指归档版本；归档仅作历史回源。实现、有效测试、原始材料、
失败、修订、研究树、锁、消息及用量账保留；清理不构成新运行或提交授权。

## 新增计划约定

1. 设计文档先按 `specs/YYYY-MM-DD-<topic>-design.md` 创建并获批。
2. 在本目录创建独立子目录，子目录名与设计主题一致。
3. 子目录以 `README.md` 作为路线图，链接回对应设计。
4. 分阶段文件命名为 `YYYY-MM-DD-NN-<step-title>.md`，按文件名排序即执行顺序。
5. 每阶段写明准确范围、前置条件、验证命令、停止门和回退边界；不得复用已删除的历史路径。
