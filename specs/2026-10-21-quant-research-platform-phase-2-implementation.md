# HETU 量化研究平台新二期实现与限定验收

> 文档版本：v1.0
> 文档状态：软件限定验收及一次独立只读评审通过；提交事实由包含本记录的收口提交及清单核对确认
> 创建／修订日期：2026-10-21
> 适用范围：现有软件独立收口；不代表原二期整体、真实数据、真实研究或经济目标通过
> 权威依据：[总需求](2026-10-13-quant-research-platform-requirements.md)、[总方案 §15](2026-10-13-quant-research-platform-design.md#15-五期交付与重组映射)、[新二期设计](2026-10-21-quant-research-platform-phase-2-design.md)
> 使用入口：[quant/README.md](../quant/README.md)；本地执行清单：[manifest.json](../.hetu/quant/acceptance/phase2-closeout-20261021/manifest.json)

## 1. 范围

用户本次明确启动已批准01→02→03，授权必要修复、验证、可恢复清理及当前分支提交；不逐单元重新报批。基线分支为 `zn_quant`，HEAD 为 `058cdc01cae28f2f921a076c0be92c7e135628db`，暂存区为空。初始未提交内容、逐文件原字节及哈希见本地清单。其他工作不覆盖，不切分支、不用worktree、不推送。

保持普通现金多头、不加杠杆、沪深普通股（含创业板／科创板）、日线估值及周／月机制；低波动正式研究仍为月频、36月、ddof=1、最低十分位、市值权重，至少10只是信号条件而非缩小原历史池的许可。研究区间2019-11-01..2026-10-13、30／42月邻域、100万元、原成本及八次预算不变。`phase2-protocol.json` 原字节和研究身份保留。

本轮收口软件、说明与证据；不采购、不联网取数、不恢复暂停采集，不计算真实信号、回测、排名或公司分析。已证行为复用，不因日期／来源身份或分期变化重验旧结果。三至五期设计文档可保留为待审交接依据，提交不授权其实施或产品采购。

流程适配：按已批准计划只维护本记录和本地manifest，不另外创建Skill进度文档／分支／worktree，不逐任务提交或强制重刷全套。01的精确处置表供02消费，02的恢复原文和限定验证供03消费；没有接口冲突。各单元实际完成状态在manifest登记，源计划最后执行版在清理前归档。

## 2. 软件行为与证据

下表是已存在的行为证据及其直接断言，不把历史局部结果相加成一次全量通过。没有直接影响的软件生产文件保留原字节；最终暂存门禁另移除两处EOF多余空行，AST不变。说明／空白问题不伪造行为红灯。

| 软件线／支持行为 | 实现与直接断言 | 有效证据及本轮处理 |
| --- | --- | --- |
| 定义与引擎：递归冻结、编解码、用途许可、隔离环境、本地真实RQAlpha | `backtest_models.py`；`test_backtest_definition.py`、`test_engine_admission.py::test_local_bundle_reaches_real_rqalpha`，次日成交及独立账户金额断言 | [许可决定](../.hetu/quant/qualification/phase2/engine-license-review.json)保留使用主体、非对外用途与用户判断；[准入](../.hetu/quant/qualification/phase2/engine-admission-20261017e/engine-admission.json)为candidate_ready、costs=full；原件及真实轨迹可回源，不采用早期blocked/skip作为通过证据 |
| 数据机制：原件哈希、公开版本、历史身份、逐对象／信号资格、完整月末、行动实施口径和补录 | `data.py`、`backtest_data.py`、`providers/baostock.py`、`providers/cninfo.py`、`probe_free_history.py`；`test_backtest_data.py`、`test_data_assets.py`、`test_cninfo_history.py` 的未来／缺失／长继承／多来源补录断言 | [多来源补录红绿](../.hetu/quant/acceptance/phase2/two-sample-qualification-20261018/verification.json)、[最新路线验证](../.hetu/quant/acceptance/phase2/free-route-20261018/verification.json)及原红灯保留；68个case为分批有效证据，非一次全量68通过；候选规范化不等于完整PIT |
| 账户执行：次日、T+1、合法价格／数量、容量、费用、完整目标退出、行动权益和税、退市限制、原子发布 | `quant/engine/`、`backtest.py`；`test_backtest_account.py` 独立账本与生产标准化→真实引擎；`test_cost_decomposition_uses_one_execution_trace`、CLI一致性 | [A1–A7账本与边界](../.hetu/quant/acceptance/phase2/review-20261018/engine-semantics.json)、[A6价格步长及滑点边界](../.hetu/quant/acceptance/phase2/review-20261018-followup/verification.json)：滑点入成交价，买向上／卖向下取整，越界拒绝；gross仅加回显式费用，不重复扣现金。复用真实引擎证据 |
| 信号／调度／评价：低波动共享计算、完整八定义、阶段和预算、语义可比基准、成熟标签、IC和评价同窗 | `strategies.py`、`run_validation.py`、`backtest_evaluation.py`；`test_backtest_formal.py`、`test_research_protocol.py`、`test_backtest_metrics.py`；变成本／池／资本拒绝，不因仅as_of／快照引用变化重跑 | [A/B执行与评价](../.hetu/quant/acceptance/phase2/review-20261018/verification.json)、[B2冻结及来源身份](../.hetu/quant/acceptance/phase2/review-20261018-followup/verification.json)，旧final-entry不覆盖；三月块／1000次／seed20261017保留。七方向是候选台账，不是已验证七策略 |
| 正式网页／worker／只读交接：冻结定义、资格、共同信号、真实进程、账户评价、找回、取消、导出与Notebook | `ui/backtest_page.py`、`worker.py`、`backtest_reporting.py`、Notebook；`test_phase2_flow.py::test_phase2_formal_web_real_engine_same_artifact_flow`、`::test_formal_worker_cancel_reaps_actual_engine_and_reopens`、未知历史／同日成交／免费失败负例 | [D/P合流](../.hetu/quant/acceptance/phase2/dp-integration-20261018/verification.json)、[原合成产物索引](../.hetu/quant/acceptance/phase2/dp-integration-20261018/synthetic-artifact-index.json)、同目录`final-direct-regression.log`：63 passed、0 skipped；网页提交dev_main，调度先基准再主规则，九表／评价／来源／Notebook同值；无付费回退，取消实际回收Popen。复用，不启动真实队列 |

上表未写目录的生产模块在 `quant/src/hetu_quant/`，测试在 `quant/tests/`。旧全量有失败时保留原数字及失败项复验，不改写为新的全量全绿：D/P全量342 passed／4 failed／0 skipped，四个材料缺池事实修正后单独4 passed；之后尾部直接回归63 passed。后续数据修复按其各自直接回归复用。

支持边界：现金分红、生产m＝新／原股数的送转、登记权益／实际到账／新增股可卖、获批2015-09-08后差别化股息税和具证现金退出已有合成真实引擎验证；不承诺配股、跨证券换股、任意税制或所有公司行动。缺退出证据保留持仓及估值限制，不按末价返现。除权限制价格参考目前仍依赖合格输入和既有适配边界，不能把原始前收当作所有除权日的合法参考；真实来源／实际事件须按三、四期补核，不宣称全部支持。

周／月机制仅对透明 `universe_vw`、warmup0按完整周期末生成真实共同信号并进入真实引擎，经济状态not_evaluated；低波动正式研究保持原月频方法。合成证券／免费事实证明软件，不代表真实研究或原论文收益。

## 3. 未完项交接

完整归属以[总方案 §15.2](2026-10-13-quant-research-platform-design.md#152-要求到分期与核心证据)的R1–R19／V1–V14和[§15.3](2026-10-13-quant-research-platform-design.md#153-原任务到新期的逐项转交)为准；下表定位实际未完行为，不另起设计或计划。原01–05未勾选项保留在第4节原文，不以删除计划改判完成。

| 原要求／原证据 | 具体未完成行为 | 新归属 | 原因 | 恢复入口 |
| --- | --- | --- | --- | --- |
| 原01.2离线清单／小样本／逐码公开版本及原05 D2-1；R2/R3/R16/V2/V11 | 原历史池所需身份、实际股本及公开／修订／期间链仍不完整；不得用少量已知样本或十只条件替代原池 | 新三期 | 来源语义缺口，不是缺实施权限；巨潮结构化候选无法独立完整PIT，补充索引仍未闭合全窗 | [来源资格](../.hetu/quant/acceptance/phase2/free-route-20261018/source-qualification.json)、[真实生产准备](../.hetu/quant/acceptance/phase2/free-route-20261018/production-qualification.json)、`probe_free_history.py` |
| 原01.2行动覆盖、账户所需字段及原02/05真实事件；R5/R6/V3/V4 | 全池operate/ex_date覆盖、登记／派息／上市日期、已知特殊内容及退出／换股等实际事件所需数据；不支持类型仍须明确处置取舍 | 新三期取字段／规则；新四期核实际事件消费 | 查询成功不证明事件完整，未知不得按无事件；不能从研究中静默删受影响证券 | [原件补录事实](../.hetu/quant/acceptance/phase2/sample-qualification-20261018/official-action-source-facts.json)、[分域限制](../.hetu/quant/acceptance/phase2/free-route-20261018/source-qualification.json) |
| 原01真实取数计划、原03范围文件及U20 | 原研究输入与二十年沪深北档案分别补齐，含离线查询、每日增量、15自然日断更恢复；分钟只评估 | 新三期（设计待审） | 149批是行动分组，5470fetch／48341名义年度查询只是旧待审清单，不是空白额度；原件／分页／修订规模仍有未知 | [增量路线](../.hetu/quant/acceptance/phase2/two-sample-qualification-20261018/incremental-products.json)、`object-batches.csv`／`batch-requests.json`、[新三期设计](2026-10-21-quant-research-platform-phase-3-design.md) |
| 原03.2范围呈报／首次多公司成本门；R11/R13/V5 | 原池、逐信号资格、各阶段八次具体定义、可复用结果、请求及费用与模型选择的本次授权 | 新四期 | 原八次是上限，真实研究0次、预算消耗0/8；来源变更不原地覆写旧冻结记录 | [后续范围文件](../.hetu/quant/acceptance/phase2/free-route-20261018/future-real-research-scope.json)、[原冻结协议](../quant/research/phase2-protocol.json)、`run_validation.py` |
| 原03.2真实执行与原05 D2-3／V5/V14；R7/R12–R14/R19 | 至少一项非基准真实样本外、30／42邻域及成本／容量压力的适用八定义；真实成熟标签、IC／暴露／统计、全部trial和经济决定 | 新四期（设计待审） | 完整真实数据未准入；不能用合成、基准或全部blocked代替真实评价，也不能以风险改善冒充收益达标 | [新四期设计](2026-10-21-quant-research-platform-phase-4-design.md)、上述范围文件、原03 §2–§3归档 |
| 原04末项真实网页核对、原05真实产物／验收；R8/R9/V1/V6 | 实际研究的网页／导出／Notebook逐值核对、报告／限制和全部试验只读交接 | 新四期 | 尚无真实研究产物；软件同产物机制已证，不为展示再模拟 | `backtest_reporting.py`、[合成同产物证据](../.hetu/quant/acceptance/phase2/dp-integration-20261018/synthetic-artifact-index.json)、新四期设计§4 |
| 原三期§3.1／§6 A及§4／§6 B | 本期真实结果只读纳入、状态分离、比较／导出提前到四期；更广研究库与完整日常体验仍未完成 | 新四期小任务；新五期其余流程 | 不重复建设现有页面，也不因迁期遗失检索／复制／使用要求 | [新四期§4](2026-10-21-quant-research-platform-phase-4-design.md#4-从原三期合入的小任务)、[新五期](2026-10-21-quant-research-platform-phase-5-design.md) |
| 原三期§3.2／§6 C；R15/V6 | 至少一项选定代表策略经平台入口真实执行及追溯 | 新五期（设计待审） | 独立脚本或只读导入不替代实际执行；完全适用的既有证据可复用，不自动重跑 | 新五期§2–§4、[原三期历史设计](2026-10-13-quant-research-platform-phase-3-design.md) |
| 原三期§5／§6 D、§7七类行为／§8成本；R17/V8/V12 | 获准真实付费源接入、完整双模式工作台和原七类适用行为；免费失败不收费，旧结果不重跑 | 新三期来源；新四期同产物；新五期体验／执行 | 产品／费用尚未批准；提交待审文档不等于采购或开发获批 | 新三期§4–§6、新五期§4、总方案§15.3原三期四行 |
| 原05证据登记、工程收尾、勾选及提交未完成项 | 软件部分由本记录及N2验收／本轮提交接替；原D2整体真实要求仍未通过 | 本期软件；三／四期对应真实部分 | 历史勾选不倒填，失败、撤回、成本超限原件保留 | 本记录§2／§5／§6及原05完整归档 |

当前数据事实：600519主36月81点、开发42月51点仍有效，2025-09-30／2025-10-31／2026-05-29股本未知继续排除；“未找到”不是证明“当时未公开”。688001仅2026-05-29新增股本消费点，2026行动版本仍受限；2022-07-29临时错误资格已撤回并保留原包。主定义逐信号最多仍1只，远非全池准入。600005于2017退出，不造2019后候选。22条旧行动、原件和父快照保留。

历史31/30小样本超额不重置，后续已批2fetch／8年度查询单列，实际请求／失败／重试／登录／raw数不混同。采集停止点保留，不恢复旧任务。数据接入、真实研究及经济状态分别仍为受限／未执行／未评价。

## 4. 清理与回源

清单按文件记录 `commit / keep / archive_remove`、理由、恢复路径和SHA256；混合或引用不明内容保留。本轮不把“不提交”理解成“可删除”。`docs/theory/A股量化数据方案评估报告.md` 的额外理论修改原地保留，不混入软件提交。

历史原文保存在本地 `.hetu/quant/acceptance/phase2-closeout-20261021/originals/`，保持仓库相对路径；清理前逐字节与哈希核对，不能声称未提交原文可从旧Git恢复。恢复时先按manifest核验归档，再读取或复制至独立位置；勿覆盖现行入口或恢复旧启动任务。

| 原文 | 回源定位／处置 |
| --- | --- |
| 原二期README及公共合同 | [规划前原文](../.hetu/quant/acceptance/phase2-closeout-planning-20261021/originals/specs/plans/quant-research-platform-phase-2/README.md)；含类型、最小列、八次原始边界及旧启动模板，仅历史 |
| 原01历史数据／引擎 | [完整归档](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/2026-10-17-01-data-engine-admission.md)；未完项见§3 |
| 原02账户 | [完整归档](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/2026-10-17-02-account-backtest.md)；早期滑点费用化、许可受阻仅是历史状态，现行边界见§2 |
| 原03研究 | [完整归档](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/2026-10-17-03-strategy-validation.md)；原冻结协议／真实要求保留 |
| 原04网页 | [完整归档](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/2026-10-17-04-web-workflow.md)；真实展示留四期 |
| 原05过程与验收 | [完整归档](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/2026-10-17-05-acceptance.md)；§5–§10保留各轮红灯、更正、失败、授权及费用，不改写原二期未整体通过 |
| 新收口组最后执行版 | [README](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/README.md)、[01](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/2026-10-21-01-scope-evidence.md)、[02](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/2026-10-21-02-fixes-cleanup.md)、[03](../.hetu/quant/acceptance/phase2-closeout-20261021/originals/specs/plans/quant-research-platform-phase-2/2026-10-21-03-acceptance-commit.md)；提交当时待办保持原样，实际执行事实见commit-result.json |

软件验收、交接和独立只读评审已闭合；README及01–03最后执行版保存在上述originals目录后退出活动目录，提交待办保持归档当时真实状态，不在归档中事后补勾提交。初始版本另保全于本地 `initial-originals/`；提交事实另见本地 `commit-result.json`。本轮9个退役计划文件原先均未跟踪，因此是工作区精确移除，不冒称Git提交删除了9个已跟踪文件。

`phase2_acquire.py`、`phase2_review_assets.py`、`probe_backtest_data.py`、`probe_engine.py`、`probe_free_history.py`各有恢复／核查／生产消费者或证据用途，全部保留，不因文件名含probe或采集暂停删除。市场raw、PDF、快照、失败.tmp、环境、fixtures、预算和证据目录均不清理；没有另选无用途临时脚本。源锁 `quant/engine/requirements.lock` 只复制运行原件原字节，运行原件和原探查消费路径保留，不安装／升级依赖。

## 5. 验收

| 编号 | 当前结论／直接依据 |
| --- | --- |
| N2-1 | 行为证据通过；§2五条软件线均有直接断言、许可／隔离真实引擎、资格不足拒绝与原始失败可回源；复用有效证据 |
| N2-2 | 行为证据通过；正式网页／worker／实际进程取消／找回／同产物导出及Notebook已证，无LLM必需依赖；本轮只读复核原合成产物 |
| N2-3 | 交接与保护通过；§3覆盖原未完项及总方案§15.2–§15.3；13旧成果、483父文件、5项冻结／台账控制均与原依据一致，真实研究仍0/8 |
| N2-4 | 必要工程检查通过；README更正、锁原字节复制、两环境pip check、ruff、空白／链接／保护及旧计划恢复核对通过；最终计划恢复及提交允许表由本地manifest逐文件核对；无环境、raw或凭据混入 |
| N2-5 | 提交事实由包含本记录的收口提交与清单核对确认，SHA见Git历史／本地commit-result.json；不在文档预填自身SHA |

本轮新验证与复用分别记录于本节，详细日志留本地收口目录；不重新生成一期榜单或真实资格／研究。只对确认新增／失败／直接影响行为补验，核心真实引擎不以mock／skip替代。

本轮没有确认需改动的生产行为缺陷；43个Python文件在独立评审时均与盘点原字节相同。随后首次完整暂存空白门禁发现 `backtest_data.py`、`backtest_cases.py` 的EOF额外空行，仅移除该空行并逐文件核对AST相同，未改变行为；[空白修正核对](../.hetu/quant/acceptance/phase2-closeout-20261021/eof-whitespace-check.json)及原失败日志保留。不编造行为红灯或重跑已通过量化套件。实际新检查：两环境 `python -m pip check` 均为 `No broken requirements found`（仅不可写pip缓存提示，未联网／安装）；`quant/.venv/bin/python -m ruff check quant` 为 `All checks passed!`。源引擎锁与运行锁均为SHA256 `2d000cae39ededadc7c7d8b6a35475c16afd5e28a02e890b9fb34b6f81ac7688`。

[只读证据核对](../.hetu/quant/acceptance/phase2-closeout-20261021/evidence-readback.json)验证两份原合成发布记录、九表ZIP／Parquet原字节一致及Notebook无执行输出，未执行第二次模拟或导出。[保护检查](../.hetu/quant/acceptance/phase2-closeout-20261021/protection.json)及[相对路径／锚点检查](../.hetu/quant/acceptance/phase2-closeout-20261021/relative-links.json)可回源；总索引五条其他任务旧本地归档缺失单列，不冒称整仓无断链。

按 `requesting-code-review` 组织的[一次独立只读评审](../.hetu/quant/acceptance/phase2-closeout-20261021/independent-review.json)通过，无阻断；独立重算13／483／5项保护、核对评审当时43个Python文件原字节与合成产物和断言；之后两处EOF空白修正由主会话核对AST及受影响ruff，未增开全面复评。唯一非阻断为总方案页首状态落后，已随现行状态更正。未重开后期数据／经济目标或全面扫描旧软件。最终恢复、链接／空白及暂存允许表核对见manifest和检查日志；本记录只有被实际收口提交包含后才证明N2-5。

## 6. 提交范围与残留

提交允许表以manifest逐文件项为准：现有软件、稳定测试、产品配置、候选／原协议、Notebook、使用说明、现行实现及重组依据。原始数据、环境、凭据、验收运行资产不进入源码提交；三至五期待审设计作为交接依据保留其待审标签。理论报告额外修改保持未提交。

实际提交前核对允许表与完整暂存差异，无他人暂存内容混入。N2-5提交事实由包含本记录的收口提交与清单核对确认，SHA见Git历史／本地 `commit-result.json`，不在源码文档嵌入自身SHA或为回填再提交。

完成后停止；不自动启动三期取数或四期研究。本轮新增市场请求、真实研究、排名、公司分析、LLM业务调用及数据支出均为0；协调模型消耗未知，不写成0。
