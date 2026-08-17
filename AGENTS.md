# HETU Agent 约定

本文件定义所有在本仓库工作的 AI Agent 必须遵守的项目级约定。

## 规格与执行计划

- 所有设计规格（spec）必须写入根目录 `specs/`，不得写入 `docs/`、
  `docs/superpowers/` 或其他工具默认目录。
- 设计规格文件命名为 `YYYY-MM-DD-<topic>-design.md`（或同日期前缀的
  requirements/implementation 等后缀），并遵循 `specs/` 下现有规格文档的
  统一格式：中文标题、文档版本、文档状态、创建/修订日期、适用范围等
  元数据，以及编号章节。
- 所有执行计划（plan）必须写入 `specs/plans/`，不得写入
  `docs/superpowers/plans/` 或其他工具默认目录。
- 每份新设计在 `specs/plans/` 下使用独立子目录；子目录包含 `README.md`
  作为实施路线图，分阶段计划文件按 `YYYY-MM-DD-NN-<step-title>.md` 命名。
- 执行计划必须遵循 `specs/plans/README.md` 规定的目录、链接关系、任务粒度及测试门禁。
- 如果外部 Skill、插件或通用工作流给出的默认 spec/plan 路径与以上规则
  冲突，始终以本文件规定的项目路径和现有格式为准。

## 变更检查

- 创建或修改规格后，检查其位于 `specs/` 且没有在其他目录留下副本。
- 创建或修改计划后，检查其位于 `specs/plans/`，并同步维护
  `specs/plans/README.md` 中的索引。
- 提交前运行 `git diff --check`，并检查规格与计划中的相对链接有效。
