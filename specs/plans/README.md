# HETU 执行计划索引

## 当前状态

2026-10-17 Standard 逐调用分析和首批机械优化局部对照已结束。分析覆盖132条用量事件、
123个工具、301个动作；原件、分项容量和候选边界继续有效。两组候选 input 分别增加
360,904（33.97%）和166,078（21.85%），工程及限定比较完成，**没有新增降本收益，不默认推广组合**。

用户要求沉淀经验并清理无用内容后，两套已完成计划及一次性分析方案已归档退出当前入口，
四个未推广候选范例及专属测试退出现行 Skill；M1–M5和现行合同保持，`check_once`失败分支
负例保留。后续参考统一见[成本分析与优化经验](../../docs/engineering/standard-token-cost-lessons.md)，
原计划及代码回源见[本次清理记录](#standard-mechanical-closeout)。
[逐调用分析](../../.hetu/validation/standard-token-comparison-20261015/call-deep-dive-20261017/report.md)、
[局部对照](../../.hetu/validation/standard-mechanical-optimization-20261017/report.md)及
[复评](../../.hetu/validation/standard-mechanical-optimization-20261017/review-20261017.md)保持原结论与限定；
清理没有新增研究、模型实验或降本证明，其他优化方向未启动。

同日此前按用户“同等效果已经确认，只缺实际输入差额及归因”的指令完成存量账目核对，
准备交付并清理旧计划。旧补件、筛样本和 G2 路径不再作为核账或新分析的前提。

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

已执行或停止执行的五期旧计划已清理。五期有效要求、实现行为、
证据与未完成边界统一见[五期实现](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md)。
详细验收与预算证据归入 `specs/validation/`，现行专项结论归入五期实现，不恢复五期旧计划。

现行事实与成果入口：

- [验收证据与批准记录](../validation/2026-10-03-phase-5-acceptance-evidence.md) §2／§6：用户批准、X1／X4 的结论与额度、本批汇总和停止投入；成本见[预算证据](../validation/2026-10-03-phase-5-budget-evidence.md) §3。
- [五期现行状态](../2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-status)：
  已交付行为、限定验收及未完成事实，不作为新增执行授权。

提交、推送与 main 集成按用户后续具体指令处理；清理不新增运行授权。

## 当前执行计划

当前没有继续执行中的计划。中国宏观历史数据对照与跨年化债的执行及授权增量补证已收尾，
部分数据覆盖仍未满足；[结果、剩余缺口及清理回源](#china-macro-history-closeout)保留原结论。

中国宏观证据采用与季度判断01／02已通过，03已按次数边界结束，
LIVE04整体验收未通过；计划原文退出工作区，未完成命题继续保留。
[本轮结果及清理回源](#china-macro-evidence-integration-closeout)不是新的研究授权。

## 已完成功能与验收

| 范围 | 结果与现行入口 |
|---|---|
| 中国宏观 Skill 传导分析完善 | 四文件、21个材料选择，D1–D4按批准范围验收；CM11三项首次通过，公司股票分析0次；[统一实现](../2026-10-24-china-macro-fundamentals-implementation.md)、[验收](../../.hetu/validation/china-macro-transmission-analysis/acceptance.md)及[计划清理回源](#china-macro-transmission-closeout)，旧通过结果及真实资料限制保持 |
| 中国宏观 Skill 研究深度完善 | 四文件、16个材料选择及V1–V6按批准范围验收；CM09边界失败最小复验、官方日收盘补证已关闭；[统一实现](../2026-10-24-china-macro-fundamentals-implementation.md)、[验收](../../.hetu/validation/china-macro-research-depth/acceptance.md)及[计划清理回源](#china-macro-depth-closeout)，旧宏观验收与更强命题限制继续有效 |
| 中国宏观基本面 Agent Skill | 01–06按已批准范围验收，四文件、13个材料选择及LIVE01–03/HIST01原件保留；[使用说明](../../docs/china-macro-skill-usage.md)、[首版验收](../../.hetu/validation/china-macro-fundamentals/acceptance.md)、[补齐验收](../../.hetu/validation/china-macro-fundamentals/completion/acceptance.md)；已完成计划见[清理回源](#china-macro-closeout)，更强全量命题与工具限制仍适用 |
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

<a id="standard-mechanical-closeout"></a>

### Standard 逐调用分析与首批机械优化清理

2026-10-17 按用户“沉淀经验、清理无用内容”的指令，移除以下已完成文档：

| 原位置 | 已清理范围 | 当前成果入口 |
|---|---|---|
| `standard-call-cost-analysis/` | README和01–03阶段计划，共4份 | [逐调用分析报告](../../.hetu/validation/standard-token-comparison-20261015/call-deep-dive-20261017/report.md)及同目录JSON／CSV |
| `standard-token-efficiency-mechanical/` | README和01–03阶段计划，共4份 | [局部对照报告](../../.hetu/validation/standard-mechanical-optimization-20261017/report.md)、[复评](../../.hetu/validation/standard-mechanical-optimization-20261017/review-20261017.md) |
| `../2026-10-17-standard-call-cost-analysis-design.md` | 已完成的一次性分析方案，共1份 | [长期经验与分析方法](../../docs/engineering/standard-token-cost-lessons.md) |

9份原文及清理前候选实现、测试和相关文档逐字节保存在
[可恢复归档](../../.hetu/validation/standard-mechanical-optimization-20261017/closeout-20261017/README.md)，
清单逐项核对大小与SHA-256；未提交原文不依赖Git历史恢复。历史证据中的原计划路径和章节号
指归档版本，不作为现行链接或待执行入口。原输入、失败、评分、成本账和研究树保持。

本次仅退役未推广的候选实现，保留旧M1–M5及新增的现行检查调用负例。负结果未改判为
优化成功；完整最终共同投入和费用限制继续保留，未来方向不自动执行。

<a id="china-macro-closeout"></a>

### 中国宏观 Skill 计划与临时资料清理

2026-10-18 按用户清理并提交工作区的明确授权，移除已完成的
china-macro-fundamentals/README.md及01–06六份阶段计划，共7份。
它们此前未提交，删除前将原字节保存至[可恢复归档](../../.hetu/validation/china-macro-fundamentals/closeout-20261018/README.md)，逐份校验大小和SHA-256。

当前使用、来源方法、验收与限制由[使用说明](../../docs/china-macro-skill-usage.md)、
[首版验收](../../.hetu/validation/china-macro-fundamentals/acceptance.md)及
[04–06验收](../../.hetu/validation/china-macro-fundamentals/completion/acceptance.md)维护。
旧记录中的计划路径/章节号指归档原文，不是当前待执行入口；原始研究、失败、修复与有效结果保留。
全平台融资、实际支付、银行分布及完整即时定价等未证命题未因清理改判，也不触发已通过研究重跑。

同时移除本任务23个临时工作区、408份临时脚本/schema及5个项目生成缓存目录。
同份原件复用和其余文件的归档去向均在清理记录中；原workspace.path作为历史定位保留。
四文件Skill、13个材料选择、构造器/消费者及其他未完成规格不删除，不修改事实或评分。

<a id="china-macro-depth-closeout"></a>

### 中国宏观研究深度已完成计划与临时文件清理

2026-10-19 按用户明确授权，移除china-macro-research-depth路线图及01–03三份阶段计划，
共4份未提交原文；先保存并逐字节核对[可恢复归档](../../.hetu/validation/china-macro-research-depth/closeout-20261019/README.md)。
当前设计、四文件、16个材料选择、构造器及消费者保留，完成依据见[本轮验收](../../.hetu/validation/china-macro-research-depth/acceptance.md)。

清理4个完成研究的临时工作区，其中38份文件与保留运行记录逐字节一致；另将4份一次性核验脚本／空提取／定位模板归档后移除，清除5个生成缓存目录。
原始来源、报告、失败、修复、上下文、实际命令及用量均保留；历史计划路径和workspace.path只作当时定位，恢复见归档清单。
本次不重跑研究或旧验收，不改变事实、评分、来源门及已证范围。用户另行明确授权本次清理后提交当前工作区改动，推送未授权。

<a id="china-macro-transmission-closeout"></a>

### 中国宏观传导分析已完成计划与临时文件清理

2026-10-20 按用户明确授权，移除china-macro-transmission-analysis路线图及01–03三份已完成阶段计划。
4份计划、4份一次性执行／核验命令及6份起始文档快照共14份原文，删除前已逐字节归档；
[清理记录、归档与恢复清单](../../.hetu/validation/china-macro-transmission-analysis/closeout-20261020/README.md)可回源。

同时清理5个已完成研究临时工作区，其中65份文件均与保留副本一致，及5个生成缓存目录。
原始报告、证据、初始消息、权限、实际调用和用量继续保留；原workspace.path只作历史定位。
清理不重跑研究，不改变事实、评分和已证边界。用户已授权本次清理后提交工作区改动，推送未授权。

<a id="china-macro-evidence-integration-closeout"></a>

### 中国宏观证据采用与季度判断执行收尾清理

2026-10-22按用户清理并提交工作区的明确授权，将路线图及01–03共4份已执行结束的计划
移出当前入口；原文未提交，删除前与7份一次性临时命令一同逐字节归档。
[归档、恢复清单和清理记录](../../.hetu/validation/china-macro-evidence-integration/closeout-20261022/README.md)
保留原计划内容，历史路径及章节号均指归档版本，不表示重新授权执行。

01材料、02规则和三项离线结果通过；原计划共7次研究，LIVE04整体未通过的事实保留。
后续R2局部修复前未复现、修复后通过，不能量化漏用率改善；原生浏览正文／错误留存仍未证。
替代连接器在独立上下文的返回留存成立；GDP短专题读取／版本／停止行为通过，数值问题部分回答，
当前精确基数及完整修订链仍缺，R2复杂联网稳定性不由该专题证明。
当前依据见[统一实现](../2026-10-24-china-macro-fundamentals-implementation.md)、
[总验收](../../.hetu/validation/china-macro-evidence-integration/acceptance.md)及
[最新增量验收](../../.hetu/validation/china-macro-evidence-integration/alternative-gdp-20261021/acceptance.md)。

同时删除10个已结束的临时工作区，其中160份文件均与保留副本逐字节相同；清理5个生成缓存目录。
原始报告、失败、输入、Skill快照、消息、工具返回、用量与验收均保留，原workspace.path只作历史定位。
清理不重跑研究或旧通过项，不改变未完成事实；公司股票分析0次。本次提交已获授权，推送未授权。

## 新增计划约定

1. 设计文档先按 `specs/YYYY-MM-DD-<topic>-design.md` 创建并获批。
2. 在本目录创建独立子目录，子目录名与设计主题一致。
3. 子目录以 `README.md` 作为路线图，链接回对应设计。
4. 分阶段文件命名为 `YYYY-MM-DD-NN-<step-title>.md`，按文件名排序即执行顺序。
5. 每阶段写明准确范围、前置条件、验证命令、停止门和回退边界；不得复用已删除的历史路径。


<a id="china-macro-history-closeout"></a>

### 中国宏观历史数据对照与跨年化债执行收尾清理

2026-10-23按用户授权，移除已执行结束的路线图和01编号计划共2份，以及4份一次性材料生成／监测文件；原文未提交，删除前逐字节保存于[归档与恢复清单](../../.hetu/validation/china-macro-history-and-debt-policy/closeout-20261023/README.md)。历史计划路径指归档版本，不是继续执行授权。

[统一实现](../2026-10-24-china-macro-fundamentals-implementation.md)、[四文件Skill](../../skills/hetu-china-macro/SKILL.md)、[使用说明](../../docs/china-macro-skill-usage.md)及26个材料选择保留。原计划4次另授权1次研究累计5次；main的V2局部遗漏、limited复验有效、旧监测偏差及所有原件均保留，不因清理改判或重跑。

[总验收](../../.hetu/validation/china-macro-history-and-debt-policy/acceptance.md)与[最新历史比较补正](../../.hetu/validation/china-macro-history-and-debt-policy/history-comparison-closeout-20261023/acceptance.md)维护实际范围：9条财政序列和跨年判断已证，8月三项同比正文遗漏及法定债12期同比／累计比较已补齐；新世纪正文已核但具体版本截止前公开时间待证，完整信用／城投可比历史和部分执行终点仍未满足。本次仅清理并提交，不新增研究、股票分析或商业操作，推送未授权。


### 中国宏观已得数据采用最小修订收尾

2026-10-24按用户授权清理并提交。当前没有新增或待删除的已完成执行计划；本次最小修订直接落实于现行研究规则和报告约定，未另写计划。

[局部验收](../../.hetu/validation/china-macro-evidence-use-20261024/acceptance.md)保留一次独立离线输出的三个行为通过范围；13处验证附件URL注记误配已另存真实取得映射，原输入输出未改，不称来源注记全面通过，也不关闭旧报告的数据缺口。

两份一次性组装／检查脚本归档后移除，计算依据、研究原件和验证记录保留；[清理回源及检查](../../.hetu/validation/china-macro-evidence-use-20261024/closeout-20261024/README.md)可回查。本次未追加研究或行为试跑，推送未授权。
