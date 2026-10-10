# HETU 产品测试材料全面清理路线图

> 执行 Agent：计划批准后使用 superpowers:executing-plans，按阶段执行并在评审点停下。
> 文档版本：v0.1
> 文档状态：已批准；三阶段执行完成（01／02 已复评通过），停整体验收点
> 创建／修订日期：2026-10-10／2026-10-11
> 适用范围：tests/product/fixtures/、直接消费者与当前引用
> 设计依据：[已批准设计 v0.2](../../2026-10-10-product-fixtures-cleanup-design.md)
> 上级索引：[执行计划索引](../README.md)
> 盘点基线：zn_dev，c9ddfaa69547d38f091e9b080f016a6f59343a7a

**目标：** 消除重复维护，使自动测试与 Agent 能选择正确材料，同时保留全部原场景、负例、阶段和通过标准。

**结构：** 官方扩展共用 valid 基准，五期并行材料按显式差异恢复；B01–B18 原文按用途保存，
静态定位表和小型 helper 仅生成当次输入。评分独立保存，历史过程按固定 Git 回源。

**技术：** 现有 Python 3.11–3.12、标准库、pytest 与 scripts/check.sh；不新增依赖。
本任务仅离线整理，不要求特定 Harness 或模型，不启动研究或业务 Agent。

## 1. 全局约束

- 用户可观察结果是少维护重复内容、正确选择材料、原覆盖继续成立；文件数不是单独完成标准。
- 范围仅为测试材料、使用方式和引用；生产规则、算法、验收标准及未取得结论保持不变。
- 不补取真实资料、不启动股票研究、不回写已锁研究及历史验收记录。沿用当前分支；
  设计、计划、实现和提交分别按用户授权推进，不新建分支或 worktree。
- 相同原文只维护一份；主体、期间、范围、单位、来源、发布／修订／生效时间、许可和复用开关显式保留。
  供应商响应、字段取证、PDF 故障保持原始形状，不从生产实现反生成依据。
- 执行输入只含任务条件和原始事实；评分、预期结论和历史评审不得进入执行输入。
  B01–B18 及增量变体保留稳定标识，按原上下文与阶段供料。
- B17 两类无旧值关闭输入保持无旧值；integration-off 故意提供的旧路径与 10.2 保留为应忽略的输入。
  B11 新时点、B14 双截面、复制时间要素和其他判定标准不省略。
- 先建立替代材料、更新消费者并验证，再删除旧副本；无 pytest 引用不是删除理由。
- 活跃材料仅移除重复路径、代理、费用和纯过程；当前有效限定与每项失败触发保留。
- 既有行为验收按原范围继续有效。路径、哈希、日期或提交变化不触发失效或重跑；
  具体语义差异先报告，按 AGENTS.md 取得最小重验授权，不用静态结构代替业务判断。
- 已批准设计中的 35–50 文件／400–650 fixture 行为估计，不是配额。
  JSON 压缩、换行或搬入代码不计实质精简；新增代码、配置与文档计入全仓净变化。

计划已获批准；各阶段任务经用户逐段授权执行（01、02 已复评通过，03 执行完成，
停整体验收点）。执行中的不确定字段或语义不能静默改写。
本路线图应用[大需求拆解方法](../../../docs/engineering/large-requirement-migration-methodology.md)
的范围、依赖、验收与回退原则；本次没有分支迁移授权，不执行该方法的迁移分支步骤。

## 2. 阶段顺序与验收

| 阶段 | 可独立交付结果 | 前置 | 必要验证／停止点 |
|---|---|---|---|
| [01 自动材料](2026-10-10-01-automated-fixtures.md) | 扩展包 5→1；并行材料 12→3，完整场景原值可恢复 | 本计划批准 | 原合同正负例、并行原值与评分隔离；停阶段评审 |
| [02 场景材料](2026-10-10-02-behavior-materials.md) | 原 90 份按用途收敛；18 case／48 选择，输入和评分隔离 | 01 通过 | 原事实、阶段、时点、变体、附件、污染负例；停阶段评审 |
| [03 引用与交接](2026-10-10-03-references-and-verification.md) | 当前入口闭合、136 原文件有去向、实际净变化及门禁交接 | 01、02 通过 | 直接回归＋一次全仓门禁＋新增文档链接／空白；停整体验收 |

01 和 02 不混同：前者验证自动材料读入，后者验证 Agent 的输入组织。
03 汇总两阶段与当前规格，不重做历史业务验收。任何阶段的局部失败不使既有成果追溯失效。
阶段评审只核对本阶段问题及直接回归，不增加假想风险或新的验收标准。

## 3. 文件责任与调用边界

| 新／改动文件（相对仓库根） | 责任 |
|---|---|
| tests/product/skill/contract_fixtures.py | 沿用现有 builder，生成三类非法扩展环境 |
| tests/product/skill/test_work_package_contract.py | 验证只持一份基准时原合同错误仍被拒绝 |
| tests/product/skill/test_phase5_parallel_contract.py | 读取并行场景并保留原断言、同源与评分隔离 |
| tests/product/fixtures/parallel/inputs/confluence.json | 七材料；转载共同原文＋各渠道差异 |
| tests/product/fixtures/parallel/inputs/returns.json | 四类返回；保持独立字段、事实和授权 |
| tests/product/fixtures/parallel/review-expectations.md | 原独立评审标准，更新当前定位 |
| tests/product/fixtures/material_cases/shared.md | 六类共同段及 T1／T2 共用的许可内原件记录 |
| tests/product/fixtures/material_cases/catalog.json | 48 个明确选择，指向任务段、事实段、附件与上下文条件 |
| tests/product/fixtures/material_cases/inputs/coverage/B01.md–B04.md | 模型深度、覆盖、查询与时点原文 |
| tests/product/fixtures/material_cases/inputs/company/B05.md–B06.md | 身份、状态和历史关系原文 |
| tests/product/fixtures/material_cases/inputs/business/B07.md–B09.md | 量价、贸易、项目履约原文 |
| tests/product/fixtures/material_cases/inputs/risk/B10.md–B12.md | 临床、司法制裁、债务原文 |
| tests/product/fixtures/material_cases/inputs/market/B13.md–B14.md | 市场、融券与双截面原文 |
| tests/product/fixtures/material_cases/inputs/acquisition/B15.md | 取得失败、限制与补充原文 |
| tests/product/fixtures/material_cases/inputs/lifecycle/B16.md–B17.md | 部分补齐、恢复和复用原文 |
| tests/product/fixtures/material_cases/inputs/safety/B18.md | 外来指令与正常补充原文 |
| tests/product/fixtures/material_cases/assets/B13/market-cap.json | 仅 B13/main/03 的计算输入 |
| tests/product/fixtures/material_cases/assets/B15/corrupt.pdf、encrypted.pdf、no-text.pdf | 原字节故障 PDF |
| tests/product/fixtures/material_cases/review-expectations/B01.md–B18.md | 原评分标准与全部增量变体 |
| tests/product/fixtures/material_cases/README.md | 当前选择、上下文、可读清单、评审入口及历史回源 |
| tests/product/skill/material_fixtures.py | 离线输入生成；不解释事实或调度 Agent |
| tests/product/skill/test_fixture_inputs.py | 输入边界、原文复用、附件和污染负例 |

区间列为便于阅读的文件族；§6 给出每个原文件的准确目的路径。
03 另更新三份现行规格的材料入口及本计划状态；具体文件和替换正文见阶段 03。
phase5_execution 六份输入共 14 行且角色不同；publication-review 九份含不同原件及独立核对链，
不为减文件数强行参数化。这是设计的复用条件判断，没有删除或降低它们的覆盖。

接口：

`materialize_input(case: str, variant: str, stage: str, output: Path, *, root: Path = ROOT)
-> tuple[Path, ...]`

生成目录必须不存在；未知选择在写文件前失败。返回 task.md、material.md、selection.json
及当次附件，作为执行者全部允许输入；不把 catalog、shared.md、完整 bundle 或评分目录交给执行者。
root 参数仅允许测试指向临时语料副本。helper 只拼接明确选中的原文，无变量替换或模板表达式。

## 4. 场景与阶段选择

| case／variant | stage | 上下文与保留条件 |
|---|---|---|
| B01/main | 01、02 | 新上下文→续派；模型／深度矩阵 |
| B02/main | 01、02、03 | 新上下文→两次补充；附注与单体不合并 |
| B03/main | 01、02 | 续派；分段窗口、开放与封闭查询 |
| B04/main | 01 | 同日异时、发布时间未知及发布周期 |
| B05/main | 01、02 | S1–S8 分别判断；S5 后续正式解除 |
| B06/main | 01 | 历史收购关系，不冒充当前登记 |
| B07/main | 01、02 | 逐指标版本；份额方法／分母独立核验 |
| B08/main | 01、02 | 分类文书补充，不从国别推公司 |
| B09/main | 01、02 | 中标、履约和收入不得互代 |
| B10/main | 01、02 | 控制关系、临床／专利及产品批准 |
| B11/main | 01、02 | 02 独立新上下文；7-02 截止，补给第一阶段原事实而非判断 |
| B12/main | 01、02 | 不同债券和授信分开；兑付链不臆推 |
| B13/main | 01、02、03 | market-cap.json 只在 03 提供；退出标的历史窗口继续必需 |
| B14/main | 01、02 | 3-31／6-30 截面分别判断；仅续派新增成分 |
| B15/main | 01、02 | 十五失败及补充；三个 PDF 仅 01 提供 |
| B16/main | 01、02、03 | 逐阶段补充；部分补齐不关闭全部根因 |
| B16/integration | 01、02 | 独立于 main；跨 owner 状态同步 |
| B17/restore | 01、02 | T1 恢复，同任务同截止；不含 T2／T3 事实 |
| B17/copy | 01 | T2 独立新任务；7-01 截止，100／10／12 原件记录保留，旧目录禁止依赖 |
| B17/closed | 01 | T3 独立自足输入；无任何旧值或路径 |
| B17/industry | 01、02 | W-A 续派；P2 库存不替代 P1 价格 |
| B17/industry-closed | 01 | W-B 独立输入；不含 P1、100 或旧目录 |
| B17/integration-on | 01 | a2 修正版；许可、渠道身份、复制时间与最新版本要求 |
| B17/integration-off | 01 | 故意存在旧路径与 10.2；禁止读取和采用 |
| B18/main | 01、02 | 页脚指令隔离，正常资料继续采用 |
| B18/integration | 01、02 | 独立于 main；恶意市场材料及正常补充 |

共 48 个选择；除 B11/02、B17/copy、B14 双截面外，截止为原材料
2026-06-30T12:00:00+08:00。context=continue 表示原会话保留已收到的前阶段资料；
独立任务仅给当次输入，canonical 规则的原读取权限保持。
B01 的请求深度和 owner 仍按原矩阵由派发者明确给定；helper 不补默认深度，
不把评分中的预期模型或结论作为任务参数。

## 5. 要求映射与验证强度

| 已批准设计 | 实施位置 | 核心结果的直接证据 |
|---|---|---|
| §1／§4 全部原件与消费者有去向 | 三阶段；本页 §6 | 136/136 行及当前引用扫描 |
| §2 相同基础＋显式差异 | 01 任务 1、2；02 任务 2 | 三类错误由一基准生成；转载全字段恢复；任务复用、共享段只一份 |
| §2 任务与评分分离 | 01 任务 2；02 任务 1、3、4 | 渲染输出无评分；误路由评分文件被拒绝 |
| §2 按选择／阶段给料 | 02 任务 1–3；本页 §4 | 48 选择逐个生成；未来值污染负例使验证失败；未知选择零产物 |
| §2 B11／B14／B17 特殊条件 | 02 任务 1、2、4 | 双截面、新时点、无旧值与故意旧线索分别检查；原标准逐项保留 |
| §3 原始响应、独立小文件 | 本页 §6；03 任务 1 | 原字节保留，已有直接用途仍有效 |
| §3／§4 历史与活跃输入分开 | 02 任务 4；03 任务 1 | B17 a2 活跃；a 初版固定 Git 回源；旧 .hetu 不修改 |
| §4 不把组织变化当成业务重验 | 各阶段停止门 | 原事实与标准映射成立；既有业务证据继承，不新增股票研究或 Agent 派发 |
| §5 真实减量与完成标准 | 03 任务 2、3 | 一次全仓门禁、原字节及引用核对、全仓净变化与保留理由 |

这次没有降低已批准要求；对“相似材料”只复用逐字相同段，保留事实不同的原件。
自动检查证明输入组织与接口，不能据此重新宣称 B01–B18 模型判断或宿主隔离认证通过。
发现具体语义差异即停止该选择的删除并报告，按 AGENTS.md 处理最小重验；不整批重跑。

## 6. 原文件逐项处置表

以下 136 行旧路径和目的路径均相对 tests/product/fixtures/；“保留原路径”指本行旧路径。
当前消费者中的测试模块位于 tests/product/skill/ 或 tests/product/validation/，明确列出的 tests/helpers 除外。
阶段结果由执行者在对应阶段交付后登记，本表本身不是已执行证明。

- V1：阶段 01 扩展合同；合法、未登记、未覆盖、重复 ID、namespace mutation 与直接复制。
- V2：阶段 01 并行七材料／四返回全字段恢复，原断言与评分隔离。
- V3：阶段 02 当前选择渲染、事实／任务完整性、时点／阶段／变体／附件边界及污染负例。
- V4：阶段 02 人工对照当前原件与全部评分标准；不重跑 Agent 判断。
- V5：阶段 03 固定 Git 原字节核对、当前引用、正式工程门禁及历史恢复。

| 旧路径 | 去向 | 当前消费者 | 原行为／用途 | 验证 |
|---|---|---|---|---|
| archive/legacy-run/broken.json | 保留原路径／原字节 | tests/helpers/test_archive.py | 损坏 JSON 失败 | V5 |
| archive/legacy-run/report.md | 保留原路径／原字节 | tests/helpers/test_archive.py | 报告导出原文 | V5 |
| archive/legacy-run/stage_results/s5.json | 保留原路径／原字节 | tests/helpers/test_archive.py | 旧阶段结果 | V5 |
| archive/legacy-run/state.json | 保留原路径／原字节 | tests/helpers/test_archive.py | 旧结构与状态识别 | V5 |
| forensic/eastmoney-field-dictionary.json | 保留原路径／原字节 | test_source_adapter.py | 原字段依据与旧标签冲突 | V5 |
| host_acceptance/synthetic-verifier-usage.jsonl | 保留原路径／原字节 | test_host_acceptance.py | 用量、调用与归属 | V5 |
| host_cli/claude.json | 保留原路径／原字节 | test_host_cli_protocol.py；test_capture_host_cli.py | 各宿主真实版本、接口与白名单 | V5 |
| host_cli/codex.json | 保留原路径／原字节 | test_host_cli_protocol.py；test_capture_host_cli.py | 各宿主真实版本、接口与白名单 | V5 |
| host_cli/native-interfaces.json | 保留原路径／原字节 | test_host_native_interfaces.py | 各宿主真实版本、接口与白名单 | V5 |
| host_cli/opencode.json | 保留原路径／原字节 | test_host_cli_protocol.py；test_capture_host_cli.py | 各宿主真实版本、接口与白名单 | V5 |
| host_cli/zcode.json | 保留原路径／原字节 | test_host_cli_protocol.py；test_capture_host_cli.py | 各宿主真实版本、接口与白名单 | V5 |
| official_work_packages/duplicate-id/WX-RND-QUALITY-COPY.md | 生成后删除 → official_work_packages/valid/WX-RND-QUALITY.md | contract_fixtures.py；test_work_package_contract.py | 重复 ID 被拒绝 | V1 |
| official_work_packages/duplicate-id/WX-RND-QUALITY.md | 生成后删除 → official_work_packages/valid/WX-RND-QUALITY.md | contract_fixtures.py；test_work_package_contract.py | 重复 ID 被拒绝 | V1 |
| official_work_packages/uncovered/WX-RND-QUALITY.md | 生成后删除 → official_work_packages/valid/WX-RND-QUALITY.md | contract_fixtures.py；test_work_package_contract.py | manifest 未覆盖被拒绝 | V1 |
| official_work_packages/unregistered/WX-RND-QUALITY.md | 生成后删除 → official_work_packages/valid/WX-RND-QUALITY.md | contract_fixtures.py；test_work_package_contract.py | 未登记被拒绝 | V1 |
| official_work_packages/valid/WX-RND-QUALITY.md | 保留原路径 | contract_fixtures.py；test_work_package_contract.py | 合法扩展与直接复制 | V1 |
| phase5_execution/annual-report-copy.json | 保留原路径／原字节 | phase5_execution_fixture.py；test_phase5_execution_contract.py | 副本五键 provenance | V5 |
| phase5_execution/failed-recompute.py | 保留原路径／原字节 | phase5_execution_fixture.py；test_phase5_execution_contract.py | 故意失败信封；不得执行 | V5 |
| phase5_execution/quotes-attempt1.json | 保留原路径／原字节 | phase5_execution_fixture.py；test_phase5_execution_contract.py | 原尝试与采用关系 | V5 |
| phase5_execution/source-task-fragment.json | 保留原路径／原字节 | phase5_execution_fixture.py；test_phase5_execution_contract.py | 独立任务片段 | V5 |
| phase5_execution/supporting-original-b.json | 保留原路径／原字节 | phase5_execution_fixture.py；test_phase5_execution_contract.py | 独立原文 B | V5 |
| phase5_execution/supporting-original-c.json | 保留原路径／原字节 | phase5_execution_fixture.py；test_phase5_execution_contract.py | 独立原文 C | V5 |
| phase5_parallel/confluence/independent-original.json | parallel/inputs/confluence.json：independent-original | test_phase5_parallel_contract.py::_load_fixture | 独立原文冲突值 | V2 |
| phase5_parallel/confluence/late-material.json | parallel/inputs/confluence.json：late-material | test_phase5_parallel_contract.py::_load_fixture | 时点后材料 | V2 |
| phase5_parallel/confluence/metric-wan.json | parallel/inputs/confluence.json：metric-wan | test_phase5_parallel_contract.py::_load_fixture | 万元值 | V2 |
| phase5_parallel/confluence/metric-yi.json | parallel/inputs/confluence.json：metric-yi | test_phase5_parallel_contract.py::_load_fixture | 亿元值 | V2 |
| phase5_parallel/confluence/normal-material.json | parallel/inputs/confluence.json：normal-material | test_phase5_parallel_contract.py::_load_fixture | 时点内正常材料 | V2 |
| phase5_parallel/confluence/reprint-a.json | parallel/inputs/confluence.json：reprint-a | test_phase5_parallel_contract.py::_load_fixture | 转载 A；同源去重 | V2 |
| phase5_parallel/confluence/reprint-b.json | parallel/inputs/confluence.json：reprint-b | test_phase5_parallel_contract.py::_load_fixture | 转载 B；独立渠道 | V2 |
| phase5_parallel/review-expectations/README.md | parallel/review-expectations.md | 独立评审者；评审隔离测试 | P03／P04／P06 原评分 | V2＋V5 |
| phase5_parallel/violations/malicious-return.json | parallel/inputs/returns.json：malicious-return | test_phase5_parallel_contract.py::_load_fixture | 恶意返回不扩权 | V2 |
| phase5_parallel/violations/normal-task.json | parallel/inputs/returns.json：normal-task | test_phase5_parallel_contract.py::_load_fixture | 完整正常返回 | V2 |
| phase5_parallel/violations/timeout-task.json | parallel/inputs/returns.json：timeout-task | test_phase5_parallel_contract.py::_load_fixture | 超时不影响他项 | V2 |
| phase5_parallel/violations/unauthorized-return.json | parallel/inputs/returns.json：unauthorized-return | test_phase5_parallel_contract.py::_load_fixture | 越权返回隔离 | V2 |
| phase6_materials/inputs/B01/stage-01.md | material_cases/inputs/coverage/B01.md＋shared.md | materialize_input：B01/main/01 | 模型与深度矩阵 | V3＋V4 |
| phase6_materials/inputs/B01/stage-02.md | material_cases/inputs/coverage/B01.md＋shared.md | materialize_input：B01/main/02 | 模型与深度矩阵 | V3＋V4 |
| phase6_materials/inputs/B01/task.md | material_cases/inputs/coverage/B01.md＋shared.md | materialize_input：B01/main/全部既有阶段 | 模型与深度矩阵 | V3＋V4 |
| phase6_materials/inputs/B02/stage-01.md | material_cases/inputs/coverage/B02.md＋shared.md | materialize_input：B02/main/01 | 主表、附注与单体覆盖 | V3＋V4 |
| phase6_materials/inputs/B02/stage-02.md | material_cases/inputs/coverage/B02.md＋shared.md | materialize_input：B02/main/02 | 主表、附注与单体覆盖 | V3＋V4 |
| phase6_materials/inputs/B02/stage-03.md | material_cases/inputs/coverage/B02.md＋shared.md | materialize_input：B02/main/03 | 主表、附注与单体覆盖 | V3＋V4 |
| phase6_materials/inputs/B02/task.md | material_cases/inputs/coverage/B02.md＋shared.md | materialize_input：B02/main/全部既有阶段 | 主表、附注与单体覆盖 | V3＋V4 |
| phase6_materials/inputs/B03/stage-01.md | material_cases/inputs/coverage/B03.md＋shared.md | materialize_input：B03/main/01 | 封闭／开放查询与窗口 | V3＋V4 |
| phase6_materials/inputs/B03/stage-02.md | material_cases/inputs/coverage/B03.md＋shared.md | materialize_input：B03/main/02 | 封闭／开放查询与窗口 | V3＋V4 |
| phase6_materials/inputs/B03/task.md | material_cases/inputs/coverage/B03.md＋shared.md | materialize_input：B03/main/全部既有阶段 | 封闭／开放查询与窗口 | V3＋V4 |
| phase6_materials/inputs/B04/stage-01.md | material_cases/inputs/coverage/B04.md＋shared.md | materialize_input：B04/main/01 | 同日异时与发布周期 | V3＋V4 |
| phase6_materials/inputs/B04/task.md | material_cases/inputs/coverage/B04.md＋shared.md | materialize_input：B04/main/全部既有阶段 | 同日异时与发布周期 | V3＋V4 |
| phase6_materials/inputs/B05/stage-01.md | material_cases/inputs/company/B05.md＋shared.md | materialize_input：B05/main/01 | 主体变体与解除链 | V3＋V4 |
| phase6_materials/inputs/B05/stage-02.md | material_cases/inputs/company/B05.md＋shared.md | materialize_input：B05/main/02 | 主体变体与解除链 | V3＋V4 |
| phase6_materials/inputs/B05/task.md | material_cases/inputs/company/B05.md＋shared.md | materialize_input：B05/main/全部既有阶段 | 主体变体与解除链 | V3＋V4 |
| phase6_materials/inputs/B06/stage-01.md | material_cases/inputs/company/B06.md＋shared.md | materialize_input：B06/main/01 | 历史关系与业务归属 | V3＋V4 |
| phase6_materials/inputs/B06/task.md | material_cases/inputs/company/B06.md＋shared.md | materialize_input：B06/main/全部既有阶段 | 历史关系与业务归属 | V3＋V4 |
| phase6_materials/inputs/B07/stage-01.md | material_cases/inputs/business/B07.md＋shared.md | materialize_input：B07/main/01 | 量价版本与独立份额 | V3＋V4 |
| phase6_materials/inputs/B07/stage-02.md | material_cases/inputs/business/B07.md＋shared.md | materialize_input：B07/main/02 | 量价版本与独立份额 | V3＋V4 |
| phase6_materials/inputs/B07/task.md | material_cases/inputs/business/B07.md＋shared.md | materialize_input：B07/main/全部既有阶段 | 量价版本与独立份额 | V3＋V4 |
| phase6_materials/inputs/B08/stage-01.md | material_cases/inputs/business/B08.md＋shared.md | materialize_input：B08/main/01 | 贸易映射与公司级边界 | V3＋V4 |
| phase6_materials/inputs/B08/stage-02.md | material_cases/inputs/business/B08.md＋shared.md | materialize_input：B08/main/02 | 贸易映射与公司级边界 | V3＋V4 |
| phase6_materials/inputs/B08/task.md | material_cases/inputs/business/B08.md＋shared.md | materialize_input：B08/main/全部既有阶段 | 贸易映射与公司级边界 | V3＋V4 |
| phase6_materials/inputs/B09/stage-01.md | material_cases/inputs/business/B09.md＋shared.md | materialize_input：B09/main/01 | 中标、履约与收入 | V3＋V4 |
| phase6_materials/inputs/B09/stage-02.md | material_cases/inputs/business/B09.md＋shared.md | materialize_input：B09/main/02 | 中标、履约与收入 | V3＋V4 |
| phase6_materials/inputs/B09/task.md | material_cases/inputs/business/B09.md＋shared.md | materialize_input：B09/main/全部既有阶段 | 中标、履约与收入 | V3＋V4 |
| phase6_materials/inputs/B10/stage-01.md | material_cases/inputs/risk/B10.md＋shared.md | materialize_input：B10/main/01 | 临床、专利与产品批准 | V3＋V4 |
| phase6_materials/inputs/B10/stage-02.md | material_cases/inputs/risk/B10.md＋shared.md | materialize_input：B10/main/02 | 临床、专利与产品批准 | V3＋V4 |
| phase6_materials/inputs/B10/task.md | material_cases/inputs/risk/B10.md＋shared.md | materialize_input：B10/main/全部既有阶段 | 临床、专利与产品批准 | V3＋V4 |
| phase6_materials/inputs/B11/stage-01.md | material_cases/inputs/risk/B11.md＋shared.md | materialize_input：B11/main/01 | 身份、原发文书、制裁及新时点 | V3＋V4 |
| phase6_materials/inputs/B11/stage-02.md | material_cases/inputs/risk/B11.md＋shared.md | materialize_input：B11/main/02 | 身份、原发文书、制裁及新时点 | V3＋V4 |
| phase6_materials/inputs/B11/task.md | material_cases/inputs/risk/B11.md＋shared.md | materialize_input：B11/main/全部既有阶段 | 身份、原发文书、制裁及新时点 | V3＋V4 |
| phase6_materials/inputs/B12/stage-01.md | material_cases/inputs/risk/B12.md＋shared.md | materialize_input：B12/main/01 | 授信、发行、评级与兑付链 | V3＋V4 |
| phase6_materials/inputs/B12/stage-02.md | material_cases/inputs/risk/B12.md＋shared.md | materialize_input：B12/main/02 | 授信、发行、评级与兑付链 | V3＋V4 |
| phase6_materials/inputs/B12/task.md | material_cases/inputs/risk/B12.md＋shared.md | materialize_input：B12/main/全部既有阶段 | 授信、发行、评级与兑付链 | V3＋V4 |
| phase6_materials/inputs/B13/market-cap.json | material_cases/assets/B13/market-cap.json | B13/main/03 生成输入 | 明确提供的市场计算输入 | V3＋字节一致 |
| phase6_materials/inputs/B13/stage-01.md | material_cases/inputs/market/B13.md＋shared.md | materialize_input：B13/main/01 | 市场计算、股本及历史融券 | V3＋V4 |
| phase6_materials/inputs/B13/stage-02.md | material_cases/inputs/market/B13.md＋shared.md | materialize_input：B13/main/02 | 市场计算、股本及历史融券 | V3＋V4 |
| phase6_materials/inputs/B13/stage-03.md | material_cases/inputs/market/B13.md＋shared.md | materialize_input：B13/main/03 | 市场计算、股本及历史融券 | V3＋V4 |
| phase6_materials/inputs/B13/task.md | material_cases/inputs/market/B13.md＋shared.md | materialize_input：B13/main/全部既有阶段 | 市场计算、股本及历史融券 | V3＋V4 |
| phase6_materials/inputs/B14/stage-01.md | material_cases/inputs/market/B14.md＋shared.md | materialize_input：B14/main/01 | 双截面与机构成分去重 | V3＋V4 |
| phase6_materials/inputs/B14/stage-02.md | material_cases/inputs/market/B14.md＋shared.md | materialize_input：B14/main/02 | 双截面与机构成分去重 | V3＋V4 |
| phase6_materials/inputs/B14/task.md | material_cases/inputs/market/B14.md＋shared.md | materialize_input：B14/main/全部既有阶段 | 双截面与机构成分去重 | V3＋V4 |
| phase6_materials/inputs/B15/corrupt.pdf | material_cases/assets/B15/corrupt.pdf | B15/main/01 生成输入 | 原 PDF 故障形状 | V3＋字节一致 |
| phase6_materials/inputs/B15/encrypted.pdf | material_cases/assets/B15/encrypted.pdf | B15/main/01 生成输入 | 原 PDF 故障形状 | V3＋字节一致 |
| phase6_materials/inputs/B15/no-text.pdf | material_cases/assets/B15/no-text.pdf | B15/main/01 生成输入 | 原 PDF 故障形状 | V3＋字节一致 |
| phase6_materials/inputs/B15/stage-01.md | material_cases/inputs/acquisition/B15.md＋shared.md | materialize_input：B15/main/01 | 合法补救与十五类失败 | V3＋V4 |
| phase6_materials/inputs/B15/stage-02.md | material_cases/inputs/acquisition/B15.md＋shared.md | materialize_input：B15/main/02 | 合法补救与十五类失败 | V3＋V4 |
| phase6_materials/inputs/B15/task.md | material_cases/inputs/acquisition/B15.md＋shared.md | materialize_input：B15/main/全部既有阶段 | 合法补救与十五类失败 | V3＋V4 |
| phase6_materials/inputs/B16/integration-stage-01.md | material_cases/inputs/lifecycle/B16.md＋shared.md | materialize_input：B16/integration/01 | 同根因部分补齐及跨 owner 同步 | V3＋V4 |
| phase6_materials/inputs/B16/integration-stage-02.md | material_cases/inputs/lifecycle/B16.md＋shared.md | materialize_input：B16/integration/02 | 同根因部分补齐及跨 owner 同步 | V3＋V4 |
| phase6_materials/inputs/B16/integration-task.md | material_cases/inputs/lifecycle/B16.md＋shared.md | materialize_input：B16/integration/01、02 | 同根因部分补齐及跨 owner 同步 | V3＋V4 |
| phase6_materials/inputs/B16/stage-01.md | material_cases/inputs/lifecycle/B16.md＋shared.md | materialize_input：B16/main/01 | 同根因部分补齐及跨 owner 同步 | V3＋V4 |
| phase6_materials/inputs/B16/stage-02.md | material_cases/inputs/lifecycle/B16.md＋shared.md | materialize_input：B16/main/02 | 同根因部分补齐及跨 owner 同步 | V3＋V4 |
| phase6_materials/inputs/B16/stage-03.md | material_cases/inputs/lifecycle/B16.md＋shared.md | materialize_input：B16/main/03 | 同根因部分补齐及跨 owner 同步 | V3＋V4 |
| phase6_materials/inputs/B16/task.md | material_cases/inputs/lifecycle/B16.md＋shared.md | materialize_input：B16/main/全部既有阶段 | 同根因部分补齐及跨 owner 同步 | V3＋V4 |
| phase6_materials/inputs/B17/integration-variant-a.md | 删除；固定 Git 回源；活跃入口用 a2 | 历史回源；当前 integration-on | 歧义初版不再活跃；历史判定保留 | V4＋V5 |
| phase6_materials/inputs/B17/integration-variant-a2.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/integration-on/01 | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B17/integration-variant-b.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/integration-off/01 | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B17/stage-01.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/restore/01、copy/01；T3 同事实由独立任务承接 | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B17/stage-02.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/restore/02 | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B17/stage-03.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/industry/01 | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B17/stage-04.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/industry/02 | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B17/task.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/restore／copy | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B17/variant-3-task.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/closed/01 | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B17/variant-w3-closed-task.md | material_cases/inputs/lifecycle/B17.md＋shared.md | materialize_input：B17/industry-closed/01 | 恢复、副本、关闭复用及行业版本 | V3＋V4 |
| phase6_materials/inputs/B18/integration-stage-01.md | material_cases/inputs/safety/B18.md＋shared.md | materialize_input：B18/integration/01 | 外来指令隔离与正常资料采用 | V3＋V4 |
| phase6_materials/inputs/B18/integration-stage-02.md | material_cases/inputs/safety/B18.md＋shared.md | materialize_input：B18/integration/02 | 外来指令隔离与正常资料采用 | V3＋V4 |
| phase6_materials/inputs/B18/integration-task.md | material_cases/inputs/safety/B18.md＋shared.md | materialize_input：B18/integration/01、02 | 外来指令隔离与正常资料采用 | V3＋V4 |
| phase6_materials/inputs/B18/stage-01.md | material_cases/inputs/safety/B18.md＋shared.md | materialize_input：B18/main/01 | 外来指令隔离与正常资料采用 | V3＋V4 |
| phase6_materials/inputs/B18/stage-02.md | material_cases/inputs/safety/B18.md＋shared.md | materialize_input：B18/main/02 | 外来指令隔离与正常资料采用 | V3＋V4 |
| phase6_materials/inputs/B18/task.md | material_cases/inputs/safety/B18.md＋shared.md | materialize_input：B18/main/全部既有阶段 | 外来指令隔离与正常资料采用 | V3＋V4 |
| phase6_materials/review-expectations/B01.md | material_cases/review-expectations/B01.md | 独立评审者 | 模型与深度矩阵：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B02.md | material_cases/review-expectations/B02.md | 独立评审者 | 主表、附注与单体覆盖：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B03.md | material_cases/review-expectations/B03.md | 独立评审者 | 封闭／开放查询与窗口：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B04.md | material_cases/review-expectations/B04.md | 独立评审者 | 同日异时与发布周期：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B05.md | material_cases/review-expectations/B05.md | 独立评审者 | 主体变体与解除链：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B06.md | material_cases/review-expectations/B06.md | 独立评审者 | 历史关系与业务归属：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B07.md | material_cases/review-expectations/B07.md | 独立评审者 | 量价版本与独立份额：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B08.md | material_cases/review-expectations/B08.md | 独立评审者 | 贸易映射与公司级边界：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B09.md | material_cases/review-expectations/B09.md | 独立评审者 | 中标、履约与收入：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B10.md | material_cases/review-expectations/B10.md | 独立评审者 | 临床、专利与产品批准：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B11.md | material_cases/review-expectations/B11.md | 独立评审者 | 身份、原发文书、制裁及新时点：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B12.md | material_cases/review-expectations/B12.md | 独立评审者 | 授信、发行、评级与兑付链：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B13.md | material_cases/review-expectations/B13.md | 独立评审者 | 市场计算、股本及历史融券：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B14.md | material_cases/review-expectations/B14.md | 独立评审者 | 双截面与机构成分去重：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B15.md | material_cases/review-expectations/B15.md | 独立评审者 | 合法补救与十五类失败：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B16.md | material_cases/review-expectations/B16.md | 独立评审者 | 同根因部分补齐及跨 owner 同步：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B17.md | material_cases/review-expectations/B17.md | 独立评审者 | 恢复、副本、关闭复用及行业版本：标准／失败触发 | V4＋V5 |
| phase6_materials/review-expectations/B18.md | material_cases/review-expectations/B18.md | 独立评审者 | 外来指令隔离与正常资料采用：标准／失败触发 | V4＋V5 |
| publication-review/evidence.md | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| publication-review/owners.md | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| publication-review/report.md | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| publication-review/request.md | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| publication-review/sources/announcements.json | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| publication-review/sources/growth.json | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| publication-review/sources/metrics.txt | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| publication-review/sources/policy.txt | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| publication-review/sources/second-channel.json | 保留原路径／原字节 | 发布前独立核对执行者／评审者 | 错误报告、owner 与原证据核对关系 | V5 |
| source_contracts/cninfo-pages.json | 保留原路径／原字节 | test_source_contracts.py；test_source_adapter.py | 原供应商 JSON 形状 | V5 |
| source_contracts/financial-report.json | 保留原路径／原字节 | test_source_contracts.py；test_source_adapter.py | 原供应商 JSON 形状 | V5 |
| source_contracts/market-snapshot.json | 保留原路径／原字节 | test_source_contracts.py；test_source_adapter.py | 原供应商 JSON 形状 | V5 |

阶段 01 结果登记（2026-10-11）：V1、V2 成立。official_work_packages 5→1
（仅保留 valid/WX-RND-QUALITY.md，四份副本字节相同、COPY 仅差 name 字段，三类环境改由
构造器从基准生成）；phase5_parallel 12→3（inputs/confluence.json、inputs/returns.json、
review-expectations.md）。test_work_package_contract.py 65 通过（新增临时环境负例先证旧实现
FAIL、替换后 PASS，删除副本后三类重跑通过）；test_phase5_parallel_contract.py 37 通过
（新增共用正文验证先证 FAIL、转换后 PASS，原断言全部保留）。11 个材料对象经与固定提交
c9ddfaa 只读对比全字段一致。2026-10-11 用户复评通过，无阻断项。Git 记录补正：该批
工作树改动为 4 个 M（三个消费者＋计划索引）与 16 个 D，此前交付报告的
「3 个 M（含计划索引）」口径作废，以本登记为准。第 02 阶段执行中。

阶段 02 结果登记（2026-10-11；同日复评通过，统计口径经用户复评补正）：V3、V4 成立。
phase6_materials 90→43 份文件（18 原文块 bundle＋18 评分＋4 附件原字节＋
shared/catalog/README）；文本行 1654→1678、字节 145630→154465，物理行与字节增长
如实计入。fixtures 总数 123→76。catalog 313 处任务／材料块引用指向 105 个唯一块；
208 为展开选择时的复用次数；原活跃输入按逐字相同段落核算，重复段副本 106 份
（旧存储去重），与多阶段引用复用分别口径。main 覆盖 17 个 case、35 个选择，
全部 18 个 case、48 个选择。test_fixture_inputs.py 14 通过（收集 FAIL 先证 helper
缺位；B16 阶段边界、B17 两类关闭输入无旧值、integration-off 故意线索、copy 许可内
原件、B11 独立新时点、B14 双截面、B13/03 与 B15/01 附件边界、未知选择零产物、
污染路由与评分误路由负例）；48/48 选择经固定提交 c9ddfaa 独立复算 task/material/
context/as_of 全等，48 选择生成后人工核对特殊变体；18 份评审依据仅指针行改 catalog
定位（31 处替换，判定标准与增量节逐字保留）；publication-review 九份与
phase5_execution 六份原字节未动。ruff、git diff --check、新增文件空白／末尾换行
全过；三测试文件合跑 116 passed 无回归。累计工作树 4 M＋106 D（阶段 01 删 16 份、
阶段 02 删 90 份），新增文件另列。第 03 阶段执行中。

阶段 03 结果登记（2026-10-11）：V5 成立。三份现行规格材料入口已更新（五期 §4.1
并行材料、六期 §11.2 行为输入、四期发布前核对补保留理由句），验收限定与历史回源
保留。旧路径扫描：tests/scripts/docs 除仍有效的 test_phase5_parallel_contract 模块名
引用外零命中；specs 命中均为历史定位或本计划自身内容。处置表 136/136 程序化对账
通过：保留原路径 30 份全部存在且与固定提交逐字节一致（git diff 基线在 fixtures 内
仅 106 D、零 M），删除 106 份与实际 D 名单完全一致。直接回归 116 passed；正式全仓
门禁 `env -u HETU_HOST_EVIDENCE -u HETU_PYTHON -u HETU_CLI bash scripts/check.sh`
退出 0（1688 passed／6 skipped 均为既有环境性 skip，ruff、mypy、check_docs、
update_skill_manifest＋MANIFEST 零变化、skill validate、git diff --check 全过）；
44 份新增／未跟踪文档显式链接与空白检查通过。净变化（基线 c9ddfaa vs 工作区，
含未跟踪、排除 gitignore）：fixtures 136→76 文件（-60）、UTF-8 行 4010→3942（-68）、
字节 266889→275268（+8379）、PDF 3 份不变；helpers_and_tests +2 文件＋254 行；
documents +5 文件＋1429 行（本设计计划自身）；other 零变化；全仓 -53 文件、
+1615 行、+117064 字节。与设计粗估差异如实说明：文件减量 -60 超出 35–50 粗估上界
（B17 歧义初版删除与 18 bundle 归并更彻底），行数 -68 低于 400–650 粗估下界
（共享段去重与 catalog／README／块标记结构性开销互抵），不为凑数调整场景。
停整体验收点，提交／推送／合入 main 未授权。

## 7. 基线、恢复与净变化

136 文件／4010 行 UTF-8 文本／266889 字节；三份 PDF 不计文本行数。
37 文件是自动消费者输入；99 文件是人工／Agent 输入和评分，不能因没有 pytest 消费删除。

按本计划明确布局：原扩展包少 4 份，并行材料少 9 份；原 90 份场景材料替换为
18 原文 bundle＋18 评分＋4 附件＋shared／catalog／README＝43 份。
fixture 预计 136→76，即少 60 文件。它是布局推算，比设计粗估更少；不是删除配额，
不能为了凑数撤掉场景。行数及字节要等实际生成、精简评分过程后统计，不承诺粗估一定达到。
helper、测试和文档增量另列，全仓净变化必须同时交付，不能只报 fixture 减量。

删除前只读核对原件与固定提交；例如：

~~~bash
git show c9ddfaa69547d38f091e9b080f016a6f59343a7a:tests/product/fixtures/phase6_materials/inputs/B17/integration-variant-a.md
git show c9ddfaa69547d38f091e9b080f016a6f59343a7a:tests/product/fixtures/official_work_packages/duplicate-id/WX-RND-QUALITY-COPY.md
~~~

需要恢复时仅恢复明确归属该阶段的文件，先比较当前用户修改；不 reset 整个分支、
不 clean、不动 Git 引用。恢复步骤是失败处置边界，不是本次允许执行的 Git 写操作。
新增或后续合法改动另行保全，不能拿固定 SHA 覆盖。历史回源不要求远端已同步。

## 8. 整体完成与交接

- [x] 01／02 阶段必要验证成立，136 原文件和消费者全部有明确去向。
- [x] 原文和任务保留，后续阶段、关闭变体及答案隔离成立；当前标准和原负例没有削弱。
- [x] 当前入口可用、直接回归及一次正式工程门禁通过；新增文件也完成链接和空白检查。
- [x] 交付共享内容、原字节保留项理由、实际文件／文本行／字节变化及新增代码／文档净变化。
- [x] 既有验收与未取得限制保留；最终停点为用户整体验收，未授权提交、推送或合并。

批准执行后默认在当前会话使用 executing-plans 串行推进；若用户另行要求子代理，
再采用 subagent-driven-development，授权不会由本计划自动产生。
