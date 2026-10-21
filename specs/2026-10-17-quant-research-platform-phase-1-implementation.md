# HETU 量化研究平台一期实现与验收

> 文档版本：v0.2
> 文档状态：一期原批准范围已于 2026-10-16 验收通过；保留后续能力限制
> 创建／修订日期：2026-10-17
> 适用范围：一期现行实现、已证结果、未完成能力及已执行计划清理后的交接
> 权威要求：[总需求](2026-10-13-quant-research-platform-requirements.md)、[一期设计](2026-10-13-quant-research-platform-phase-1-design.md)
> 总方案：[量化研究平台方案](2026-10-13-quant-research-platform-design.md)
> 使用入口：[安装、网页与 worker](../quant/README.md)
> 后续现行入口：[新二期软件实现与限定验收](2026-10-21-quant-research-platform-phase-2-implementation.md)；完整数据与真实研究交接新三／四期，设计待审；原分期要求与审批记录保留

## 1. 现行结论与批准范围

一期按原批准范围验收通过：真实免费输入已产生行业、主题各五个板块、每板块五股的正常两榜，网页、导出与 Notebook 共用结果，成熟策略准备资产和关键反例齐备。低波动／E/P 的真实数值条件测试仍未完成，经济有效性尚未评价；这些限制不改变原合同中“策略准备通过、条件不合格如实受阻”的验收结论，也不代表整个三期平台已完成。

用户于 2026-10-13 批准总需求和总方案 v0.6，随后批准一期设计与计划；2026-10-14 的暂不执行要求在后续明确启动后解除。最终真实运行按 `p1-final-run-scope-20261015.json` 的单次范围获批。二、三期设计及计划未获执行授权；清理计划、提交代码均不新增数据请求、股票分析、策略或排名重跑授权。

## 2. 已交付的实现与合同

同仓独立 Python 3.11 子项目位于 `quant/`，不把量化依赖加入旧单股环境。Streamlit 提供个人网页，Pandas 计算，Parquet 保存输入和结果，DuckDB 读取，SQLite 保存定义和任务，由本地 worker 执行长任务。精确依赖见 [pyproject.toml](../quant/pyproject.toml) 和 [requirements.lock](../quant/requirements.lock)。

| 能力 | 实现入口与现行行为 |
| --- | --- |
| 数据请求与免费资格 | [models.py](../quant/src/hetu_quant/models.py)、[data.py](../quant/src/hetu_quant/data.py)：`DataRequest`、`SnapshotRef`、`QualityReport`；`DataStore.prepare/import_files/qualify/read_table`。按产品、对象、期间和用途判断，网络、缓存、本地导入共用准入；当前只启用 free，paid 明确拒绝 |
| 来源与资产 | [providers](../quant/src/hetu_quant/providers/__init__.py)、[产品配置](../quant/config/data-products.json)：BaoStock、AKShare 和本地文件；raw 保留原始返回，标准化另存；查询代码域与返回股票分区域分别记录，成功空响应留证，失败不登记覆盖 |
| 增量与恢复 | 按分区 coverage 期间差集补缺；复制、合并保留覆盖与来源证明，复用前核验哈希；旧资产仅从已核验 request/raw 证据恢复可证明的覆盖，无法证明才补取。空分区和非空分区按名称统一 schema 读取 |
| 研究计算 | [ranking.py](../quant/src/hetu_quant/ranking.py)、[metrics.py](../quant/src/hetu_quant/metrics.py)：`run_ranking` 消费冻结定义及快照，分别计算行业、主题、板块内股票；数据不足发布 limited，不伪装正常数量 |
| 定义、任务与网页 | [experiments.py](../quant/src/hetu_quant/experiments.py)、[jobs.py](../quant/src/hetu_quant/jobs.py)、[worker.py](../quant/src/hetu_quant/worker.py)、[app.py](../quant/src/hetu_quant/ui/app.py)：保存定义时冻结完整协议；同次提交幂等，终态后的新点击生成新提交标识；数据更新也进入持久队列。取消、中断、重试保留原尝试，不自动重跑已完成任务 |
| 证据与导出 | [evidence.py](../quant/src/hetu_quant/evidence.py)、[reporting.py](../quant/src/hetu_quant/reporting.py)：数值依据、反证、风险、缺口、下一核验与人工 Gate；无 LLM 可运行。`export_result` 只读结果，ZIP 导出 JSON、CSV 和研究卡 Markdown |
| 独立策略准备 | [低波动脚本](../quant/research/low_volatility.py)、[E/P 脚本](../quant/research/ep_value.py)、[策略协议](../quant/research/protocol.json)：原方法与本地适配分开，36 个月收益预热与检验区间分开；红利低波只作储备；`economic_status=not_evaluated` |

公共类型、用途、签名以 `models.py` 及上述模块为准。快照存于 `.hetu/quant/snapshots/<id>/`，结果存于 `results/<id>/`，任务索引为 `catalog.sqlite`。发布后不原位覆盖；标识和哈希用于来源记录及同一产物完整性核验，不作跨运行等价、过期或追溯失效判断。

## 3. 冻结研究规则

具体规则保存在 [ranking-defaults.json](../quant/config/ranking-defaults.json)，提交时保存完整协议；以下保留原计划中已批准、实际实现的关键选择，不另行优化参数。

| 项目 | 现行规则 |
| --- | --- |
| 窗口与时点 | 最近三个自然年背景、六个自然月重点；共同 `cutoff` 和带 Asia/Shanghai 时区的 `as_of`。自然月边界取当日或之前最近市场交易日作锚点；不按单股缺失平移窗口、不静默退一天 |
| 资格 | 必需半年交易日、三月／半年锚点及 60 个日收益所需价格完整；必要窗口未知比例为 0。截止日或半年内明确停牌列观察；ST／退市排除。最近 20 日成交额中位数至少 2,000 万元、60 日成交额总和大于 0；短于三年但满足必要窗口可排名并标明实际历史 |
| 收益口径 | 排名为价格收益；送转／拆并股机械跳变作连续处理，保留现金除息影响。低波动准备另用税前除息日再投资总收益，不能自动改用价格收益。未知行动覆盖或无法核实的复杂条款限制相关用途 |
| 分类与成员 | 申万一级单一分类；东财概念候选须通过题材库名称命中及 v2 逐条核验的 industrial_theme 双条件。核验表缺失／为空、名称未命中、类别未知或非产业主题一律排除，不回退旧规则。主题至少 5 个合格成员且合格数占当前全部成员至少 80%；行业须有合格指数且可选股至少 5 只 |
| 四维分数 | 三月／半年相对沪深 300 价格指数收益百分位的均值为趋势；半年最大回撤和 60 日样本波动率取反向百分位；60 日上涨日成交额占比为量能。四维各 25%，缺失不补零，排名用未舍入值 |
| 分组与席位 | 行业按指数特征比较；主题按合格当前成员原始特征中位数比较；股票在所属板块全部合格成员内比较。先选板块再选股，两类各 5×5；并列同名次，边界按代码稳定截取并说明；重复股票保留关系，不去重补位 |
| 解释边界 | 当前成员回溯特征不能称为历史主题收益；无历史成员不输出历史宽度。宽度按合格当前成员站上最后 60 个交易日均线的比例；集中度为等权正收益集中代理。价格分数不自动改变人工行业 Gate，不表示上涨概率或组合收益 |

结果有九张固定 schema 表：`boards`、`slots`、`stocks`、`metrics`、`exclusions`、`series`、`cards`、`overlap`、`aux`。即使零席位也能查看和导出；空表不改变结果受限事实。Notebook [read_result.ipynb](../quant/notebooks/read_result.ipynb)读取同一发布结果。

## 4. 真实运行与直接验收证据

以下为已有验收事实，运行证据保留在本机 `.hetu/quant/`，不会随源码提交；干净检出可运行离线测试，但不因此获得真实数据资产或新的来源许可。

| 必需结果 | 已证行为与证据 |
| --- | --- |
| 免费数据资格 | 已有逐产品来源、字段、单位、时点与覆盖探查；免费失败、付费凭据及导入改名均不绕过准入。原探查 `qualification/probe-20261014T014143.json`，对应 `test_free_boundary.py`、`test_time_and_coverage.py` |
| 正常真实两榜 | 快照 `prep-20261015T234146-67cb7fc4`；排名任务 `job-20261016T002323-aa3a3919`，结果 `res-20261016T005732-fad1f089`，status=complete；截止日 2026-10-13，行业／主题各 5 个板块、各 5 席，共 50 席、45 只独立股票、4 个重复关系 |
| 时点与数值 | 数据取得于 2026-10-16 00:07:35，排名定义在 00:23:23 冻结，排名不含 obtained_after_as_of。准备任务取数前提交产生的该提示仍保留在原任务。手算、扰动及缺末日反例见 `test_metrics.py`、`test_ranking.py`、`test_common_cutoff.py`、`test_price_basis.py` |
| 网页与导出 | AppTest 无错误，显示两榜、重复、基准、证据卡、Gate 与限制；ZIP 九张 CSV 的列、行、值与发布 Parquet 一致。证据 `acceptance/p1-final-20261015T231746-web-export-evidence.json`；任务恢复、取消、Notebook 同产物另见 `test_jobs.py`、`test_web_workflow.py`、`test_phase1_flow.py` |
| 策略准备 | 三张策略卡、两份可执行脚本、冻结协议、合成机制／信号验证和真实条件受阻报告齐备；`research/lowvol-real-20260930/`、`research/ep-real-20260930/`保留 blocked 与 not_evaluated，不虚称真实信号或净收益通过 |
| 授权、成本与旧结果 | 最终补取严格为获批 528 次 provider.fetch，全部成功且无清单外请求；530 仅为名义上游接口估算。排名 1 次，公司深研 0、LLM 业务调用 0、付费数据 0 元。旧结果保护基线 13 个文件哈希不变，清单 `acceptance/p1-old-result-protection-baseline-20261015.json` |

2026-10-14 结果 `res-20261014T185953-e6d3f53b` 的 limited、25 个行业席位与当时主题分类缺口继续作为原时点记录保留；`acceptance/p1-acceptance.json` 是该次历史记录，不能单独代表后续现行结论。首次运行接口调用估计 38,027 次，17,931 为 raw 文件数，不能当请求数。

253 个晚起始代码已于 2026-10-16 离线核实：原始／标准化上市日期一致，506 次成功空查询均结束于上市前，首个非空日线／状态日期等于上市日，1,014 个资产哈希通过。逐代码证据为 `acceptance/p1-late-start-verification-20261016.json` 与同名 CSV；未决 0。该结论只解释这些上市前缺口，不证明全部历史完整，不缩窗口、不修改已发布快照。

## 5. 已关闭的修复与生产证据

原计划 05 保存的四轮修复及后续复评已完成：对象／期间行动资格、增量覆盖、旧 raw 证据恢复、覆盖复制、空表读取、精确 MA60、月末日历覆盖、提交标识、协议冻结、网页异步、raw 分层与主题分类接通均有针对性回归。稳定 provider fixtures 保存在 [材料说明](../quant/tests/fixtures/providers/README.md)，不依赖本机运行资产。

2026-10-16 的七项证据更正保留历史原件：市值 available_at 改记重建取得时间；旧 976 个空响应仅解释原 SDK 预案公告年查询范围；删除“只能等10月30日／必须合同豁免”的过强判断；北交所旧错误输出不再作为通过依据，另存离线重放更正记录。54 次是顶层探查调用（11 失败），明确重试 1 次，另有 1 次同端点调用意图未证；名义 HTTP 已知小计 83 次，另 22 次顶层调用底层次数未知，实际网络请求与传输重试总量未知。

最新生产接缝已通过复评：BaoStock 实际成功的 `yearType=operate` 查询（包括空响应）输出 `query_evidence`，DataStore 在 raw 和新分区持久化 `query_coverage`；完整实施日期证明才可标 `query_basis=ex_date`。复制保留证明，合并保留各段口径和期间，公告年／未知旧段不升级。低波动逐股消费完整所需窗口证明。

`test_operate_query_reaches_low_volatility_through_real_prepare` 的六个空／非空、已知／未知／公告年场景走真实 fetch→standardize→prepare→策略准备，仅替换网络；正例未用 helper 补写证明，并核验复制、合并、重开与拒绝旧段。该轮必要回归 76 passed；没有真实供应商请求、策略计算或排名。详尽逐项日志及历史红灯记录保存在第 7 节原计划归档与原运行资产。

## 6. 能力限制与后续交接

| 项目 | 仍未满足的最小范围 |
| --- | --- |
| 策略真实条件测试 | 免费市值产品准入已核验，历史公开时点资格仍未证；当前实现未消费“较晚取得、另证信号时已公开”的证明路径。E/P 缺历史原始财务公告与修订版本链。低波动／E/P 真实数值测试及完整经济评价均未完成；2026-10-30 仅为待资格核验候选，不是必须等待的日期 |
| 北交所完整覆盖 | bj_basic／bj_daily／bj_actions 已接入；更正验证只以已保存响应离线重放 fetch/standardize，未证明新在线全量分页、完整 prepare 或排名。351 只全量历史未取，历史 ST／停牌／退市字段与配股仍有缺口；既有榜单不声称全 A 股完整覆盖 |
| 主题依据的覆盖 | 已存发布者定义和逐条核验可追查；题材库 2026-10-16 复测 667 条与前日相同。部分被测详情页返回 404，首页仍可达并有迁至 APP 公告，不外推整个路由永久下线。新主题定义覆盖不足继续按未知排除 |
| 公司行动完整性 | 新适配器按实施年 operate 查询有实测依据；旧 report／默认查询的公告年证据不随代码升级变成实施年证明。配股和其他复杂条款缺口继续限制相关研究用途，独立样本不外推全体 |

二期承接可信账户回测、交易制度、成本后与样本外经济验证及更广策略检索；三期承接工作台与复用。设计审批和真实运行成本门仍按 [AGENTS.md](../AGENTS.md)执行，不能为清理或提交重跑已有榜单。

## 7. 已执行计划清理与原文恢复

2026-10-17 按用户指令清理 `specs/plans/quant-research-platform-phase-1/` 的路线图和 01–05 五份已执行计划，共六份。现行设计保留，已交付行为和验收由本文件接替；二、三期未批准设计保留。没有删除测试 fixtures、已发布数据、历史失败或原验收文件。

六份计划此前未进 Git，删除前已逐字节复制至本机 `.hetu/quant/acceptance/cleanup-20261017/originals/specs/plans/quant-research-platform-phase-1/`，并在 `cleanup-20261017/manifest.json` 登记原路径、归档路径、大小和 SHA256。该目录被 Git 忽略，原计划不在源码提交中；原文须从此本地归档恢复，不能宣称可从旧 Git 提交回源。归档还保留本轮修改前的文档，便于核对清理没有改写原批准要求。

旧运行证据中的计划路径与章节号均指归档前版本，不作为待执行入口。恢复时按 manifest 校验并读取相应 `originals/<原路径>`；勿覆盖现行文档或因此重启已结束任务。现行入口与后续计划组织见[执行计划索引](plans/README.md)。
