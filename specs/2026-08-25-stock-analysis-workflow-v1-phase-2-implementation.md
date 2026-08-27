# HETU 个股分析工作流 V1 二期实现

> 文档版本：V1 二期实现 v1.3
> 文档状态：V1 二期实现说明；G1 与实现已完成，G2 未完成
> 创建日期：2026-08-25
> 修订日期：2026-08-27
> 一期基线：[V1 一期实现](2026-08-17-stock-analysis-workflow-v1-phase-1-implementation.md)
> 长期需求：[V1 长期需求](2026-08-17-stock-analysis-workflow-v1-requirements.md)
> 方法依据：[代码简化方法论](../docs/engineering/code-reduction-methodology.md)
> 适用范围：canonical `hetu-stock-analysis` Skill 的 `quick`、`standard`、`deep` 单股研究
> 权威顺序：canonical Skill 与源码 > 当前测试 > 本文档

---

## 0. 当前结论

二期在一期 Agent 主导工作流之上落地了四类能力，研究语义仍全部由 Agent 控制：

1. **输出合同**：固定十二章报告、首页元数据（含分析模型）、核心发现表、W0–W10 独立提交和
   七态状态语义，全部写入 canonical Skill 的按需 references。
2. **产物合同**：固定研究目录、封闭 `manifest.json` Schema、产物命名模板、中间脚本留存和
   从报告到来源的追溯链。
3. **确定性工具与来源**：五个原子脚本（财务标准化、公告索引、数值一致性、财务比率序列、
   行情序列指标）、三来源封闭采集 `source_fetch.py` 与四域九状态的来源适配器；五域首选/替代
   来源合同与等价边界。
4. **验收设施**：无状态产物检查器、repo 级锁定帮助器和验证语料（适配器重放、数值工具
   输入输出、语义回归批次）。

最终状态（截至 2026-08-27）：

- G1 启动审计完成：七份历史报告的五份只读分析齐全，十二章、工具候选和来源范围均由样本
  证据与用户确认支撑。
- **G2 最终验收未完成**：绑定实现身份后的五次隔离真实报告、单份批次终审和最终用户批准
  尚未执行；这是二期唯一未闭环的部分。
- 两个已批准例外保持真实状态：负向因果门禁为 `FAIL`，冻结记录未完成且旧记录失效；两者仅被
  豁免为 G2 前置条件，不得改写为通过，也不被 G2 结果反向追认。

本文只保留现行实现、最终验收结果和有效限制；阶段过程、修订历史和已执行计划经 Git 历史追溯。

## 1. 与一期的关系

一期全部边界保持不变：Agent 负责请求理解、来源选择、冲突裁决、回访和写作；Python 不控制
研究语义；`hetu-stock` CLI 仍只有 `skill validate/install` 与两个 helper。二期只新增约束、
工具和验收设施：

| 职责 | 所有者 | 裁决性质 |
| --- | --- | --- |
| 报告结构、覆盖与模型补充 | Agent + canonical Skill references | 语义，Agent 裁决 |
| 产物登记、命名与追溯 | Agent 按 references/artifact-contract.md 执行；检查器机械复核 | 结构，检查器判定 |
| 采集/解析/标准化/精确计算/一致性比较 | 五个确定性脚本（显式参数，单次原子任务） | 机械，脚本判定 |
| 来源请求的结构化适配 | `source_adapter.py`（保存响应 + 显式请求 → 信封） | 机械，适配器判定 |
| 换源时机、替代采用、等价判断 | Agent（读来源合同后决定） | 语义，Agent 裁决 |
| 产物完整性与锁定 | 检查器 + 锁定帮助器 | 机械 |

脚本不可用只形成局部缺口，不让 public 研究整体失败；`phase2_lock_run.py` 是 repo 级工具，
不导入 Skill 内部模块。

## 2. 报告输出合同

### 2.1 固定十二章

三档深度共用连续编号二级章节：任务与时点、核心发现、公司业务与行业、治理审计资本配置与
重大事件、财务验证与经营质量、预测与受限情景、估值与隐含预期、市场状态与近期信号、论点
反证未知与条件、监控建议、数据覆盖缺口冲突与来源、最终边界。

单次运行不得新增、删除、改名或重排二级章节；无适用内容时保留章节并使用 2.4 节状态语义。
"2. 核心发现"紧接重要声明。

### 2.2 首页元数据与核心发现表

第 1 章两列表格固定记录：证券与核验后身份、`as_of`（带时区）、实际分析或定稿时间、分析
模型（宿主未暴露时如实写"未暴露"）、推理深度、数据模式、请求深度与实际覆盖、技术完成状态、
研究目录。

核心发现表固定列为"维度、状态、核心发现、用户可读证据、正文定位"，固定十行覆盖主体与
交易状态、核心业务、经营规模、经营回报、现金或资产质量、治理与重大变更、并购与资本配置、
估值与市场状态、最强反证、关键验证点。不适用维度保留并说明理由。核心发现只压缩正文与已
采用工作包内容，不新增事实；表内关键事实必须在正文展开。合格模型补充可增加"其他重要发现"
行，不得改名或挤占固定行。

### 2.3 表格与模型补充

固定维度、连续时序和多项比较优先使用 Markdown 表格（财务序列、情景、估值、市场状态、
监控、覆盖来源六类有最小字段表）；因果分析、论点、反证、未知、条件和边界用必要文字，
不为表格形式删除限定语。

模型补充有且仅有三种合法形式：统一"模型补充发现"区、所属章节下连续编号三级小节、紧邻
论述的补充表格。补充项必须未被必填字段覆盖、有证据或标注判断、实质影响理解/论点/边界、
可定位到工作包和来源；没有合格内容时整个补充区省略。反复出现的高价值补充只进入演进候选，
未经需求修订和用户批准不得自动升级为固定字段。检查器接受三种形式，不因未出现统一补充区
判定缺失。

### 2.4 状态语义

七态：**有**（有效证据）、**替代取得**（批准替代来源满足用途，记录原失败与边界）、**无**
（完整检索后确认不存在）、**未发现**（仅索引或有限筛查）、**未取得**（主要与合理替代均
失败）、**不适用**（经营/会计/交易状态理由）、**存在冲突**（未裁决差异，保留双方口径）。
"未检索到"不得写成"无"；重大变更不得从正文静默省略，除非完成规定窗口筛查。

### 2.5 三档覆盖

`quick` 覆盖主体状态、核心业务、最新财务概览、近期公告、股本行情、基础估值输入、支持/
反证/最大未知、验证点与缺口；`standard` 增加控制关系、业务结构、驱动、行业竞争、治理审计、
资本配置、调整后多年财务与最新一期全量、情景估值、市场状态、论点条件与带指标的监控；
`deep` 再增加更长历史、完整年报附注、分部产业链、更多独立来源、敏感性与交叉估值。深度以
覆盖和证据判断，不以字数或表格行数判断。

### 2.6 最终消息一致性

最终用户消息只能呈现或复述 `report.md` 内容，不得含报告外独有事实或更完整总结；这是 G2
独立裁决的直接比较项。

## 3. 工作包与产物合同

### 3.1 目录与所有权

每次研究使用 `.hetu/research/<证券标识>-<任务时间>/`，含 `checkpoint.md`、`evidence.md`、
`manifest.json`、`work-packages/W0..W10`、`artifacts/{raw,normalized,derived,scripts}` 和
`report.md`。工作包不得修改其他工作包文件；共享文件按职责更新。同任务恢复用原目录，独立
重试用新目录，不覆盖失败或已交付运行。

### 3.2 工作包公共结构

每个 W 文件包含连续章节：任务元数据、目标与实际覆盖、输入与共享基准、已核验事实与证据
定位、计算、分析判断、缺口冲突失败与替代、下游输出与回访条件、脚本清单、模型补充、完成
边界与自检、修订记录。共同字段不留空；无适用内容用状态语义并说明原因。W10 只汇总达到
终态、通过自检且无未解决阻断的结果，上游缺失回访 owner 或披露缺口，不在汇总时补造。

### 3.3 manifest.json

封闭 Schema：顶层 `schema_version` + `run` 块（run_id、证券、`as_of`、模式、深度、模型、
runtime_skill、时间）+ 非空 `artifacts`。每条记录封闭键集：相对路径、类型、媒体格式、工作
包（W0–W10）、SHA-256、source_id、期间或 `as_of`、创建时间、Schema/计算版本、`inputs`
输入数组（逐条路径+哈希；外部采集脚本可空但须在脚本元数据记录端点）、状态（adopted/
superseded/failed/not_adopted）；仅两个条件键——`script` 对象（仅 type=script；`output`
必须指向已登记产物，不能用占位文字）与 `failure`（仅 status=failed）。

相对路径不得含 `..` 或指向研究根外。不同来源、期间、Schema、口径或重试分别成文件；失败
产物同样登记。派生文件名 hash8：单输入取其哈希前 8 位，多输入按路径排序拼接后聚合哈希
前 8 位——仅为可读提示，manifest 输入哈希为权威身份。命名模板偏离只产生检查器 warning，
不否定内容质量。

### 3.4 追溯链与脚本留存

`report.md → W10 映射表 → owner W*.md → evidence.md → manifest.json → 来源`；确定性计算
还须定位 `derived/` 与输入哈希。W10 映射接受完整标题、编号或连续范围等等价写法；人可读
来源已闭合而机器定位不完整时记 warning，不冒充事实错误。

`artifacts/scripts/<W>/` 保存实际执行与失败版本脚本，manifest 登记用途、安全调用方式、
输入输出、环境依赖、退出状态与哈希（`dependencies` 接受非空字符串或数组）。新实测任务的
W0 任务卡包含逐字"保留中间脚本"提示；无脚本时记录"无中间脚本"。含 secret、绕过逻辑或
无关数据的脚本不得保存或执行。

### 3.5 回访修订

回访在"修订记录"追加旧值、新值、原因、新证据和受影响下游，不删除已被下游采用的旧事实、
失败或冲突；上游变化由 owner 回访并递归复核下游，不能只改 `report.md`。

## 4. 确定性脚本与来源

### 4.1 五个工具

| 脚本 | 输入 | 输出 | 边界 |
| --- | --- | --- | --- |
| `financial_statements.py` | 已保存财务响应 | 保留原值与报告期的标准化数据 | 不猜字段、同比或调整口径 |
| `announcement_index.py` | 已保存公告页 + 时区化 `as_of` | 索引、总数、返回数、分页完整性 | 不决定检索窗口与采用 |
| `numeric_consistency.py` | 显式数值、单位、容差 | 双方值、差异、一致性 | 不改写来源值 |
| `financial-ratio-series.py` | 标准化财务 + 主体/期间/范围/单位/输入哈希 | 利润率、现金转化、CAGR、营运资本、资产质量 | 不选口径、不解释结果 |
| `market-series-metrics.py` | 已保存行情 + 复权/时区/`as_of`/窗口/输入哈希 | 收益率、均线、区间高低、回撤 | 不生成交易信号 |

共同合同（`_artifact_io.py`）：显式 `--input/--output`，成功信封含 `input_sha256` 与
`source` 透传；失败写结构化失败信封并退出 1；输入不可读、参数缺失或输出已存在退出 2 且
不写输出。同一保存输入字节级可重放；输入变化产生不同产物身份。

#### 4.1.1 封闭来源采集

`source_fetch.py` 固定一次 CLI seam：
`python source_fetch.py --input REQUEST.json --output SAVED_RESPONSE.json`。只有 Agent 已按来源合同
明确选择来源后才调用；每次只采集一只规范 A 股证券的一个来源。采集成功不代表证据可采用，
适配成功也不代表来源等价；来源选择、检索窗口、替代采用、冲突裁决和业务解释仍归 Agent。

输入顶层严格为 `schema_version="1"`、`source_id`、大写 `NNNNNN.SZ|SH|BJ` 主体、带时区且不晚于
当前时点的 `as_of`、匹配来源的 `purpose` 和封闭 `request`：

| `source_id` | `request` | 固定产物 |
| --- | --- | --- |
| `cninfo-announcement-index` | `start_date`、`end_date`；北京时间日期且结束日不晚于 `as_of` | 解析组织标识，保存按序闭合的原始公告分页 |
| `sina-financial-statements` | `statement=profit|balance|cash`、`period_count=1..20` | 保存 `sina_report_list` 原始容器，不改名或猜测科目 |
| `tencent-quote-snapshot` | 空对象 | 保存单证券 GBK 原文及机械解码的证券、时点、价格、市值和原位标签 |

脚本只访问 `www.cninfo.com.cn`、`quotes.sina.cn`、`qt.gtimg.cn` 的固定 HTTPS 路径；同 allowlist
重定向逐跳校验。它不接受任意 URL、请求头、Cookie、token、代理或可执行输入，不自动重试、
换源、补采或扩大请求。巨潮/新浪只接受规范化 `application/json`，腾讯只接受规范化
`text/html`；响应和分页总字节上限为 8 MiB，公告最多 100 页且任一页失败都不发布部分成功。

成功输出回显请求、主体、`source_metadata`、HTTP 状态和 adapter-ready `body`。403、429、其他
非 2xx/传输错误、结构或媒体类型错误分别写稳定的 `permission_denied`、`rate_limited`、
`transport_error`、`parse_error` 失败信封并退出 1；输入/CLI/输出冲突退出 2。输出在同目录临时
文件完整写入并 `fsync` 后以硬链接原子发布，既有或竞态目标不覆盖，失败不留截断目标或临时残留。
Agent 负责确保输入和输出位于同一当前研究根，且输出只在 `artifacts/raw/`；脚本本身不接受研究根
或任意写入授权。

canonical `SKILL.md` 只把该能力列为按需工具；`references/tool-catalog.md` 拥有接口和限制，
`references/source-contracts.md` 拥有来源身份、入口、字段、许可和适用边界，
`references/host-tools.md` 拥有宿主工具分工。`MANIFEST.json` 和三宿主安装树包含同一脚本与 references。
最终三项真实 fetch 均成功保存；adapter 的 `success`、`success`、`out_of_asof` 均与实际响应一致，
符合九态合同，但不替代 G2 所需的隔离真实报告和终审。

东财字段误标、腾讯市值标签互换、资金流 Schema 覆盖、硬编码、HTTP/单页与补采依赖六类错误，
现由当前接口行为回归直接保护。退役实现可在 Git 历史追溯，但不是测试或运行输入；各具名保护
以 `references/tool-catalog.md` 的现行映射为准。

### 4.2 来源适配器

`source_adapter.py` 对保存响应 + 显式请求输出信封，四个结构化域（公告及附件、财务报表、
同业市场估值、交易状态与市场快照）各配首选/替代合同（`references/source-contracts.md`）；
行业需求与竞争保持合同-only，不建适配器。九种状态：
`success`、`not_found`、`rate_limited`、`transport_error`、`permission_denied`、`parse_error`、
`incomplete_pagination`、`out_of_asof`、`scope_mismatch`；`disabled_sources` 命中时短路为
`called=false, disabled=true`。

等价判定带轴报告：请求/载体/正文声明的主体、期间、单位逐轴比较，可解析则给 `match`
布尔，不可解析的描述性标签如实记 `match=null`（未裁定），不作假等价也不作假冲突。快照域
`as_of` 阶梯：带时刻按时刻、同日无时刻返回 `content_after_asof=null`（未裁定）。

首选失败由 Agent 分类后显式选择替代；替代值须与 L0 核验；不等价保留缺口是正确行为。已确认
接入的缺口域：官方交易状态、独立行业份额/竞争证据、公告分页与同日发布时间。未披露订单是
披露缺口，无合法等价来源时保持缺口；单日涨跌归因与全天资金流不建设正式适配器。

### 4.3 archive 退役裁决

`archive/phase4-analysis-tool-candidates/` 已整体删除，不再是运行、测试或冻结输入。裁决如下：

| 历史内容 | 最终裁决 | 现行保护 |
| --- | --- | --- |
| 三个已由 canonical 同名工具吸收的候选实现、八个旧测试及其 fixture/日志 | 删除，不迁移副本 | 现行工具测试、确定性重放和旧 case→当前 pytest node 映射 |
| 巨潮公告、新浪财务、腾讯单证券行情采集 | 不复制旧聚合脚本，仅把独立机械价值重写为 `source_fetch.py` | 封闭 Schema、HTTPS allowlist、分页、失败、原子发布和真实采集验证 |
| EastMoney 搜索/数据中心/资金流、百度、同花顺、新闻、批量同业、一次性补采 | 无稳定合同或扩大产品边界，删除且不接入 | 禁止任意来源、静默补采、批量和 fallback 的负向测试 |
| 报告拼接、全流程聚合、历史 raw/report 与 archive README | 与 Agent 主导或现行运行无关，删除 | 报告/工作包合同及 Git 历史 |

旧实现的字段误标、市值标签互换、同名覆盖、硬编码证券/日期/路径、HTTP/单页和静默补采六类
错误由当前接口行为直接保护，不再以错误代码是否存在作为测试输入。archive 不得改名、复制或
移动到其他仓库目录恢复为现行材料。

## 5. 验收设施

### 5.1 产物检查器

`skills/hetu-stock-analysis/scripts/check-run-artifacts.py`，无状态单次调用
`--research-root/--delivery-message/--lock-record/--output`。十二项检查：lock schema、消息
存在、消息与研究树哈希重算绑定、必需目录与文件、manifest 条目与封闭 Schema、产物格式
（json/csv/py 可解析）、报告十二章结构与固定表、lock 身份绑定（manifest↔lock 的模型/
runtime/技能）、W10 追溯映射、脚本登记（owner 限 W0–W10，实际与失败版本或"无脚本"声明）、
数据产物登记、锁辅助路径哈希。

硬错误进 `issues`（稳定 code 如 `manifest.schema`、`report.missing_fixed_table`、
`trace.invalid_w10_mapping`、`lock.message_hash_mismatch`）；不影响事实或完整性的机械差异
（等价表头、文件名风格、章节简称、机器定位不完整）进 `warnings`。只有 issues、未锁定消息
或失败硬检查导致 `mechanical_status=FAIL`；机械 warning 不把内容 PASS 改成 FAIL。退出码
0 干净 / 1 有问题 / 2 输入不可读或冲突；输出经临时文件+原子硬链接写入。检查器不判断自然
语言事实真假、消息一致性或来源适用性——这些归独立裁决。

### 5.2 锁定帮助器

`scripts/phase2_lock_run.py` 把最终消息逐字复制到 `<batch>/locks/<run_id>/` 并写
`lock-record.json`：请求、研究树（冻结树哈希：拒绝 symlink，POSIX 相对路径按字节排序，
逐文件写路径+NUL+SHA-256+NUL）、报告、消息、环境、可见文件哈希及 runtime/Skill/模型身份。
拒绝不安全 run id、symlink 路径与既有目标；staging 目录内二次校验输入未变后，以 mkdir
预留 + 双硬链接原子发布，竞态目标不被覆盖。无裁决字段。树哈希与检查器逐字重复是部署边界
（repo 工具不导入 Skill 内部模块），按设计保留。

### 5.3 哈希用途限制

哈希只用于：同一封存版本的完整性、manifest artifact 身份、确定性工具输入输出、实际
runtime/Skill/checker 身份。不用于跨报告内容比较、质量评分或要求独立运行相同；不同运行的
`as_of`、请求字节和报告哈希本可不同。

### 5.4 验证语料

`.hetu/validation/`（gitignored）保存可复算语料：34 份适配器重放输出（绑定当前
`source_adapter.py` 哈希）、数值工具 38 份输入输出（绑定当前 `numeric_consistency.py`
哈希）、语义回归批次、G1 五份分析与三份已锁定的 v6 运行。检查器对三份锁定运行的输出作为
行为差分基线，任何检查器改动必须逐字节复现。

### 5.5 来源采集与退役保护

`test_source_fetch.py` 直接覆盖封闭输入、固定请求、公告分页和中途失败、财务/行情原值保存、HTTP
分类、媒体类型、逐跳 allowlist、无 retry/fallback、原子发布冲突和无残留；`test_source_adapter.py`
与现行工具测试保护字段字典、市值标签和 adapter-ready 链，`test_deterministic_tool_io.py` 封闭 Skill
脚本集合。自动测试通过私有 transport seam 完成，CI 不访问真实网络。

退役完成条件是 `archive/` 与历史专用测试消失，运行范围无旧路径引用，canonical 与三宿主安装树
文件及 SHA-256 一致，完整门禁、三来源隔离真实采集、总跟踪文本行/字节净减少和依赖不变同时成立。
真实来源不可达可如实形成九态证据，但三项均至少有一次成功保存和适配前不得宣布采集能力完成。

## 6. 验收状态、例外与假设

### 6.1 已完成

- G1：七份历史报告的 run-index、报告格式、缺口、脚本候选、模型补充五份分析完成并获用户
  确认（十二章、两个新增工具候选、三个缺口域接入均由样本证据支撑）。
- 全部输出/产物/工具/来源合同进入 canonical Skill 并通过安装完整性校验。
- 一轮保护性减量（2026-08-25）：测试 −183、检查器 −11、公告工具 −2；零孤立项；三宿主全新
  安装与 canonical 逐项一致；全部行为差分与消失证明留存。
- 来源采集与 archive 退役：三项封闭采集已进入 canonical Skill 并完成离线、真实和安装验证；
  17 个 archive 跟踪文件、历史专用测试和遗留空目录均已删除，总跟踪文本行/字节相对冻结基线
  净减少，依赖集合不变。
- 第二轮测试保护性减量最终净减 375 行（16105 → 15730，9 个变更文件）；最终完整门禁
  997 passed，生产代码、canonical Skill、Prompt、references 与检查器行为不变。结果低于原
  800 行下限，用户已明确接受该终值；G2 状态不受影响。

### 6.2 未完成（G2）

二期结束还缺：绑定 Git commit、Skill 树与 manifest 哈希后的五次隔离真实报告（`quick`、
`deep` 各至少一次，同一中性 `standard` 目标在三个不同可核验模型上各一次，各自实际
`as_of`）；每个获批缺口域一次受控禁用首选的真实切换；单份 `batch-acceptance.md` 批次终审；
必要复验与最终用户批准。请求只含研究目标、深度、时点、逐字留存提示和模型记录要求，不含
章节、表格或预期答案。终审发现硬错误并修改实现时，受影响运行失效并重跑。

### 6.3 已批准例外（保持真实）

负向因果门禁为 `FAIL`，冻结记录未完成且旧记录失效。依用户 2026-08-24 决定，仅豁免二者的
G2 前置效力；产品行为由五次真实报告直接验收。相关证据不得改写为通过，G2 结果也不反向追认。

### 6.4 假设与已知限制

- 三宿主正式认证仍为 `UNVERIFIED`（一期限制延续）。
- 启动样本无 `quick` 报告与模型字段，属已接受历史限制；`quick` 行为基线在 G2 首次建立。
- 精确市占率与未披露订单在无合法等价来源时保持缺口，不以相邻数据冒充。
- `references/tool-catalog.md` 已更新为现行测试映射；该映射只说明既有保护归属，不改变 G2
  的未完成状态或上述已批准例外。

## 7. 不存在的结构（负边界）

以下不是当前能力，也不得作为入口或设计依赖：Python/Jinja 报告渲染器与结论拼接、S0–S9
状态机与 conductor、固定业务结论 Schema、来源插件系统或通用数据框架、`hetu-stock` 新
CLI 命令、后台静默换源、以多数票自动裁决来源冲突、历史报告追补或改写、绕过登录/验证码/
付费墙/反爬/许可。EastMoney 搜索/数据中心/资金流、百度、同花顺、新闻聚合、批量同业和报告
拼接均未接入；`collect_*` 聚合脚本、`build_*`、`stagekit.py`、`local_pubcheck.py` 不得接入。

## 8. 维护者导航

| 主题 | 契约/源码 | 主要测试 |
| --- | --- | --- |
| 报告与模型补充 | `references/report-guidance.md` | `test_phase2_output_contract.py`、`test_prompt_resources.py` |
| 工作包结构 | `references/work-package-result.md` | `test_work_package_contract.py` |
| 产物与 manifest | `references/artifact-contract.md` | `test_phase2_output_contract.py`（schema validator + demo batch） |
| 工具合同与重放 | `references/tool-catalog.md`、`scripts/_artifact_io.py` | `test_tool_cli_contract.py`、`test_deterministic_tool_io.py`、`test_*_tool.py` |
| 封闭来源采集 | `references/tool-catalog.md`、`references/host-tools.md`、`scripts/source_fetch.py` | `test_source_fetch.py`、`test_deterministic_tool_io.py` |
| 来源适配 | `references/source-contracts.md`、`scripts/source_adapter.py` | `test_source_adapter.py`、`test_source_contracts.py` |
| 已知错误回归 | `references/tool-catalog.md`、字段字典 fixture 与 canonical 脚本 | `test_source_adapter.py`、`test_numeric_consistency_tool.py`、`test_deterministic_tool_io.py`、`test_announcement_index_tool.py`、`test_source_fetch.py` |
| 产物检查器 | `scripts/check-run-artifacts.py` | `test_artifact_checker.py` |
| 锁定帮助器 | `scripts/phase2_lock_run.py` | `tests/product/validation/test_phase2_lock_run.py` |
| 研究运行 fixture | `tests/product/skill/phase2_run_fixture.py` | — |

完整门禁 `./scripts/check.sh`：pytest 全量、Ruff、mypy、文档链接与旧命令检查、
manifest 重建无差异、canonical Skill 校验、`git diff --check`。源码或 Skill 变化时先更新
对应 owner 与 manifest，再同步本文；冲突时以源码、Skill 和最新门禁结果为准。
