# 工作包目录

W0–W10 是单只 A 股基础研究的稳定核心工作包。编号用于定位和覆盖检查，不代表执行顺序；
Agent 根据依赖、证据、工具与用户关注点按需读取、自由编排和回访。

| ID | 名称 | 覆盖角色 | 适用条件 | 链接 |
| --- | --- | --- | --- | --- |
| W0 | 任务界定与约束 | required | 每次研究都适用 | [W0](core/W0-task-framing.md) |
| W1 | 证券与发行人核验 | required | 每次研究都适用 | [W1](core/W1-subject-verification.md) |
| W2 | 增量披露与重大事件 | required | 每次研究都适用 | [W2](core/W2-incremental-events.md) |
| W3 | 行业与竞争环境 | required | 每次研究都适用 | [W3](core/W3-industry-competition.md) |
| W4 | 业务、治理与资本配置 | required | 每次研究都适用 | [W4](core/W4-business-governance.md) |
| W5 | 财务验证 | required | 每次研究都适用 | [W5](core/W5-financial-validation.md) |
| W6 | 预测与情景 | required | 每次研究都适用，允许在严格条件下标记包级不适用 | [W6](core/W6-forecast-scenarios.md) |
| W7 | 估值与隐含预期 | required | 每次研究都适用 | [W7](core/W7-valuation-expectations.md) |
| W8 | 市场状态与近期信号 | required | 每次研究都适用 | [W8](core/W8-market-signals.md) |
| W9 | 论点、反证与监控 | required | 每次研究都适用 | [W9](core/W9-thesis-counterevidence.md) |
| W10 | 报告与发布自检 | required | 每次研究都适用 | [W10](core/W10-report-review.md) |

## 官方扩展协议

官方扩展使用 `WX-<DOMAIN>-<NAME>` ID，文件位于 `official/`，元数据必须使用
`kind: official-extension` 与 `coverage_role: supplemental`。每个扩展必须登记在本目录、
受 `MANIFEST.json` 覆盖，并通过依赖、共享基准、回访和链接结构校验。扩展不得覆盖核心 ID、
削弱安全规则、豁免核心覆盖、独立发布报告或取得整次研究控制权。

`required_when` 仅由 Agent 判断；Python 不解释条件或执行依赖。二期 canonical 包当前官方
扩展数量为零；测试 fixture 中的扩展示例不属于可安装 Skill。
