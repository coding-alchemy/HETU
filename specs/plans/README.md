# HETU 执行计划索引

## 历史与未启动范围

已完成的历史阶段（Agent 主导闭环、代码精简、独立质量加固和股票分析指南数据源分层整合，
实现提交 `7d9ce7d`、评审收尾提交 `ae74657`）计划与设计已从工作区删除，只通过 Git
历史追溯；已落地行为统一见
[个股分析工作流 V1 一期实现](../2026-08-17-stock-analysis-workflow-v1-phase-1-implementation.md)。

V1 二期加固和 V1 长期需求目前只有需求，不得把需求文档当作已批准执行计划：

- [V1 二期加固需求：报告模板约束与质量加固候选](../2026-08-17-stock-analysis-workflow-v1-phase-2-hardening-requirements.md)
- [V1 长期需求：完善个股分析工作流](../2026-08-17-stock-analysis-workflow-v1-requirements.md)

## 新增计划约定

1. 设计文档先按 `specs/YYYY-MM-DD-<topic>-design.md` 创建并获批。
2. 在本目录创建独立子目录，子目录名与设计主题一致。
3. 子目录以 `README.md` 作为路线图，链接回对应设计。
4. 分阶段文件命名为 `YYYY-MM-DD-NN-<step-title>.md`，按文件名排序即执行顺序。
5. 每阶段写明准确范围、前置条件、验证命令、停止门和回退边界；不得复用已删除的历史路径。
