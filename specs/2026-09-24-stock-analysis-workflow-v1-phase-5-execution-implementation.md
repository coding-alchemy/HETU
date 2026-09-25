# HETU V1 五期下一批迁移实现：上下文管理、正式并行与重复工作减少

> 文档版本：v1.1
> 文档状态：迁移经用户人工确认；阶段 02 全面评审及 R1 复评通过；阶段 03 最终备份与
> 文档整合已执行并通过评审；已通过 PR #9 合入 main（`751ed53`）；阶段 04 源分支清理与
> rebase 已执行并复评通过；未声明五期整体完成
> 创建／修订日期：2026-09-24
> 适用范围：原第 1 组上下文管理剩余部分、第 3 组正式并行与汇合、第 4 组三项优化的
> 选择性迁移（从 `zn_dev` 提取到以最新 main 为基点的 `zn_p5e`）
> 固定功能来源：`zn_dev=b42b9dda9f57b57002b1509bfa6010d134157e8e`
> 实际主线基点：`main=c4e52581e911e395a6e90eaf4cf1ef9af0dca971`
> 文档性质：本批迁移要求、实现入口、验收依据与适用限制的统一权威文档；替代本主题的
> 迁移设计、证据摘要与执行计划目录三份过程文档（最后内容固定于备份分支
> `zn_p5e_plan@6d5bb4b`，该分支不得合入 main；去向见 §6）
> 权威顺序：源码与 canonical Skill > 当前测试 > 本文档

---

## 0. 当前结果

本批从 `zn_dev` 选择性迁移三组已实现功能到以最新 main 为基点的 `zn_p5e`，为 Skill 规则
补入三种用户可观察的行为：

1. **原第 1 组剩余（长材料分批读取与整理）**：按问题控制单批字节量，必要全文分批读完
   不丢例外、否定与条件；压缩或转交保留决定所需事实与限制；只减少已证明重复的读取。
2. **原第 3 组（正式子任务并行与汇合）**：两个以上独立问题才评估并行；自足输入、最小
   权限与隔离写入；汇合裁决去重同源、统一单位、冲突回源；局部失败隔离与越权处置。
3. **原第 4 组（三项减少重复工作的优化）**：任务内共享取数只取一次；汇合不重做充分且
   适用的已完成部分；定稿自检完成后实际向用户呈现报告或可打开定位，再验收锁定与锁后评分。

阶段 01 迁移完成并经用户人工确认；阶段 02 首轮评审发现一项 P2 问题 R1（正式子任务派发
未传递主任务实际复用开关及边界），已按批准修复并复评通过，无未关闭阻断项（见 §3、§5）。
阶段 03 完成固定最终方案备份与本文档整合，并通过 PR #9 合入 main（`751ed53`）。
阶段 04 已完成 zn_dev 已迁增量清理与 rebase，交付 `a044bb2`，复评通过；远端同步另行授权。

既有证据为受控合成样本（L1–L3、P01–P06）、一条 ZCode 受控短链路（B2）与 2026-09-17
三项优化短链路，各有明确限定范围（见 §4）。**宿主原生压缩效果、容量可见路径实测、
经长语料验证的单批安全读取量与真实并行收益仍未验证；不宣称长上下文验收通过、全部宿主
通过或性能收益达标。** main 的 F1/F2、M1/M2 修复与既有断言全部保留；CLI 仍为 10 个叶子。

## 1. 目标、范围与原八组去向

本批是已有功能的选择性迁移，不新增调度平台、压缩引擎、缓存框架或计量系统，不把完整
性能达标作为本批功能交付条件；迁移前后要求、例外、优先级、实际行为与证据适用范围一致。
沿用已完成的安装维护（F1）、旧成果安全导出（F2）、任务控制恢复（M1）和旧资料复用（M2）。

| 原组号与名称 | 本批处理 | 迁移后的去向 |
|---|---|---|
| 1 研究控制、恢复与上下文管理 | 只迁入剩余的长材料、上下文整理与转交规则；M1 已在 main | 功能规则完成迁移；未验证的原生压缩效果及安全读取量仍如实披露，不宣布全部宿主长上下文验收通过 |
| 2 旧任务复用与产物追溯 | M2 已在 main，作为依赖保留 | 不重复迁移或重开验收；复用收益仍归原第 8 组 |
| 3 正式并行与汇合 | 本批迁入 | 交付既有功能与受控行为证据，收益比较仍归原第 8 组 |
| 4 三项减少重复工作的优化 | 本批迁入 | 交付共享取数、汇合不重做、交付先于锁后评分三项行为 |
| 5 第三方扩展管理 | 不迁入 | 继续留在 zn_dev，既有实现与缺口保留 |
| 6 计量与宿主验收工具 | 不迁入 | 生产工具及关联缺陷留 zn_dev；中期已关闭的历史对账和预算方法不重开 |
| 7 研究质量与宿主认证 | 不迁入 | 深度、多模型及完整宿主认证等专项保持原去向，不成为本批新增门禁 |
| 8 正式性能采样与校准 | 不迁入 | 不自动续开样本，不宣称提速、token 节省或数值预算已校准 |

子条款、缺陷、证据限制与迁移步骤均不另计为需求组；已迁与未迁行为不重复计为两组需求。
Linux/ext4 安装验证按用户 2026-09-24 明确决定不再作为当前交付任务，保留未实测的平台
边界披露；这与原第 7、8 组的暂缓交付不同，不据此宣布这些专项已从五期全部取消。

## 2. 十一行行为：完整规则、实现入口与验收依据

权威历史依据是原五期设计 §2.3、§2.5.3–5 及 2026-09-17 三项优化记录，可从固定提交
`c0c8c53` 的 `specs/2026-09-06-stock-analysis-workflow-v1-phase-5-design.md` 回源
（本地：`git show c0c8c53:specs/2026-09-06-stock-analysis-workflow-v1-phase-5-design.md`）。
下表保留设计 §3 十一行的全部条件、例外、先后顺序与禁止项；实现入口相对
`skills/hetu-stock-analysis/`，测试相对仓库根。测试文件简称：capability＝
`tests/product/skill/test_phase5_capability_contract.py`；parallel＝
`tests/product/skill/test_phase5_parallel_contract.py`；prompt＝
`tests/product/skill/test_prompt_contract.py`。

| 所属组／行为 | 必须保留的完整规则 | 实现入口 | 验收依据 |
|---|---|---|---|
| 1／分批与完整覆盖 | 按问题控制每批字节量，每批读后立即处理；必要全文分批读完，不丢例外、否定、条件；50,000 字节仅未校准参考，不变成获批上限 | `references/host-tools.md`「长材料分批读取与整理」第 1–2 段 | capability：`test_byte_budget_is_uncalibrated_reference_with_per_batch_continuation`、`test_batching_does_not_truncate_must_read_rules_or_skip_full_documents`；L1–L3 实际读取与回答（§4） |
| 1／整理与转交 | 保留原 checkpoint 全部更新时机，材料规模与可见容量／压缩信号只增加整理；无信号不猜余量 | 同节第 3 段；`references/checkpoint.md`「更新时机」末两句 | capability：`test_consolidation_triggers_preserve_checkpoint_update_timing`、`test_capacity_visible_and_unknown_take_different_routes`；L3 容量未知路径（不冒充可见信号实测） |
| 1／决定保真 | 保留证券、时点、模式、授权、来源、计算、事实、冲突、缺口、失效记录和用户决定（十一项），与目标／预算／采用一致；跨批主体、版本、单位不混淆；摘要不足先回源，不存隐藏思维链 | 同节第 4–5 段 | capability：`test_versioned_material_dimensions_survive_batches_and_compression`、`test_compression_handoff_preserves_invariants_and_rereads_source`、`test_per_host_safe_read_amounts_registered_and_unverified_not_claimed`；L1 转交保留、L2 丢否定负例、L3 回读拒绝编造 |
| 1／有限减少重复读取 | 只减少已证明重复的规则读取、同问题检索和长材料解析；独立核对自行回源、回访重开、摘要不足和未覆盖必读项仍实际读取；沿用原第 1 组，不增列第四项优化 | `references/host-tools.md`「重复读取的有限减少」 | capability 负向合同：`test_read_reduction_limited_to_proven_duplicates_with_required_coverage` |
| 3／派发决策 | 主 Agent 固定主体、时点、模式、深度、关注点、授权及证据规则；两个以上独立问题才评估并行；按剩余问题分工，不凑域数；依赖、共享状态、受限资源和高冲突场景先串行；不改为固定 DAG | `references/orchestration.md`「正式子任务与并行派发」（门槛、派发前六项固定、默认串行与依赖先行） | parallel：`test_new_section_sits_after_temporary_subquestions_without_rewriting_them`、`test_formal_subtask_identity_lifecycle_and_adoption_reuse_existing_contract`、`test_parallel_threshold_requires_independent_stateless_problems`、`test_predispatch_unifies_six_items_and_selects_domains_by_question`、`test_seven_stages_default_serial_and_dependencies_come_first`；P01–P06 决策样本；B2 实际派发与依赖顺序 |
| 3／输入与写入边界 | 自足输入的十二项、返回的十三项完整保留；最小上下文和权限；公共输入只读、可写文件不交叉；子任务不写 manifest／checkpoint／evidence／owner 正文／报告；不新增目录层级 | 同节自足输入十二项、最小上下文段；`references/host-tools.md`「子任务派发与最小权限」 | parallel：`test_self_contained_inputs_list_twelve_items_with_minimal_context`、`test_subtasks_never_write_shared_files_and_returns_merge_serially`、`test_host_tools_dispatch_section_keeps_minimal_permissions`、`test_subtask_artifacts_stay_in_owner_dirs_named_by_task_and_attempt`；B2 公共输入只读、隔离写入 |
| 3／裁决与采用 | 审查完成度、来源、时点、授权、证据类型及结论边界；同源去重、统一单位、冲突回源，不按多数／速度／自报置信度采用；authorized 返回重新核对来源、许可、点时、必要性与脱敏 | `references/orchestration.md`「汇合裁决与失败隔离」裁决段与 authorized 再审段 | parallel：`test_adjudication_section_is_pure_insertion_after_dispatch`、`test_confluence_adjudication_reviews_six_dimensions_dedups_and_unifies`、`test_majority_speed_and_self_reported_confidence_are_not_adoption_bases`、`test_authorized_results_are_rechecked_before_merge_not_on_completion_claims`、confluence 夹具三测试；P03/P04/P06；B2 合入与独立核验 |
| 3／局部失败与追溯 | 在预算内重试、缩小、重派、串行或局部降级；失败／未采用版本保留；恶意、越权返回不执行不采用；依赖未就绪不综合；无子 Agent 时同质量串行并说明限制；取消迟到结果按 M1 处理；复用 main 的任务／尝试／采用校验，不另建状态机 | 同节失败隔离段；`references/recovery.md`「并行失败隔离与越权处置」；`references/work-package-result.md` 首段串行合入新增句 | parallel：`test_failure_isolation_limits_blast_radius_with_budgeted_options`、`test_synthesis_waits_for_decidable_state_and_satisfied_dependencies`、`test_no_subagent_fallback_is_same_quality_serial_and_marked`、`test_record_verification_keeps_relations_live_and_failure_evidence`、`test_recovery_isolation_block_is_pure_insertion_after_reuse_section`、`test_recovery_block_rejects_unauthorized_and_malicious_returns_in_isolation`、violations 夹具两测试；B2 重派、正常任务继续、恶意材料不执行 |
| 4／共享取数 | 指定唯一首次取数责任，登记后按路径只读复用；先核对主体、期间、单位、用途，复用适用解析；新版本、新时点、疑似来源变化与独立交叉取证仍按规则取数；与旧任务开关互不替代 | `references/orchestration.md`「正式子任务与并行派发」共享取数段 | parallel：`test_in_task_shared_sources_check_applicability_and_assign_single_first_fetch`；20260917 短链路判定 1 |
| 4／汇合不重做 | 充分且适用的已完成部分不重研／重算，只补实际冲突、缺口及受影响结论；统一表达、跨域检查、来源追溯和独立核验全部保留 | `references/orchestration.md`「汇合裁决与失败隔离」合入核对段之前的新增段 | parallel：`test_confluence_merges_from_returns_without_redoing_completed_parts`；20260917 短链路判定 2 |
| 4／及时交付 | 定稿前独立核对、修正、直接影响复查及安全自检完成后，实际向用户呈现报告或可打开定位，再锁定与锁后评分；逐样本呈现，内部通知／标记文件不替代；不削减首次完整独立核对或修正后直接影响复查 | `SKILL.md`「最终交付」末段；`references/work-packages/core/W10-report-review.md` 自检第 4 项 | prompt：`test_delivery_presentation_precedes_lock_and_post_lock_scoring`；20260917 短链路判定 3（含两轮订正，原生记录证明「可见交付→锁定→评分工具执行」） |

另：capability 的前四个测试
（`test_capability_precheck_covers_inventory_and_unknown_versions`、
`test_missing_required_capability_degrades_waits_or_stops_by_existing_rules`、
`test_independent_verification_entry_is_fixed_with_nested_fallback`、
`test_first_verification_complete_and_fixups_recheck_direct_impact_only`）
约束 main 已有的能力预检与独立核对入口，随文件整体迁入；迁入前已核对 main 入口行为
一致，未复制重复实现。

`tests/product/fixtures/phase5_parallel/` 十二文件原样迁入；`review-expectations/README.md`
仅评审侧，答案未注入 `confluence/` 或 `violations/`（由
`test_review_expectations_live_only_on_the_review_side` 约束）。

## 3. R1 修复：正式子任务继承实际复用开关与对应边界

阶段 02 首轮评审发现：新的正式子任务派发契约未明确传递主任务实际
`reuse_previous_task_data` 及其边界，而此前明确的传递要求只写临时子任务；父任务为
false、正式子任务仅收到十二项约定输入时，子任务可能按默认 true 使用旧资料——这是对
main M2 的集成保护缺口（未声称已观察到实际污染）。修复经用户批准后实施于 `2566c5e`，
复评于 `ba0fea5` 通过，R1 关闭：

- `references/orchestration.md`「正式子任务与并行派发」派发前固定段末：首次派发与重派前
  主 Agent 均向子任务传递本次已确定的实际 `reuse_previous_task_data` 开关及对应边界，
  子任务不得以默认值替代主任务已确定的选择；`false` 时子任务同样不查找、不读取、不采用
  旧任务资料与上下文中的旧结论，**本任务内合法取得并登记的共享输入仍按共享取数规则可用**。
- `references/recovery.md`「关闭复用的上下文边界」：传递范围由“临时研究子任务和交付前
  独立核验”扩为“临时研究子任务、正式子任务（含首次派发与重派）和交付前独立核验”。
- `references/host-tools.md`「研究前能力预检」：`false` 时预检确认能否传递禁用边界的
  对象同步覆盖正式子任务（含重派）。

即：**临时子任务、正式子任务（首次派发与重派）与交付前独立核验均继承主任务实际复用
开关与对应边界；当前任务合法共享输入仍可用；M1/M2 其他要求不变。**

回归与保护：新增
`test_phase5_parallel_contract.py::test_formal_dispatch_and_redispatch_pass_actual_reuse_switch_and_boundaries`
（先红后绿：旧规则下检出 `ValueError`，修规则后通过；断言限定在具体节内并检查适用范围
与顺序）；`test_phase5_reuse_contract.py::test_reuse_false_boundary_passed_to_subtasks_and_independent_review`
的传递句断言按新文句更新为完整新句，临时子任务与交付前独立核验两项原接收方仍逐项点名，
未删除、skip 或弱化任何断言。

对固定来源的有意差异：上述三个 Skill 文件因 R1 修复不再与
`b42b9dda9f57b57002b1509bfa6010d134157e8e` 逐字一致，差异仅限本条所列语句；其余迁入
内容与源逐字一致的结论不变。

## 4. 历史行为证据与保留限制

本批初始状态是“实现已存在、已有合同与受控行为证据”；不因历史计划已勾选就推断所有
宿主能力通过，也不因历史观测局限推翻已观察到的行为。原始证据位于本机
`.hetu/validation/phase-5/`，只读保留、不入库；工程门禁不依赖 `.hetu/`。

| 证据入口 | 可沿用结论 | 保留限制 |
|---|---|---|
| `20260911-stage03-execution/l-samples/`（L1–L3） | L1 七类语义区分及十一项转交保留；L2 丢否定负例检出；L3 实际四批读取、回源与拒绝编造 | 不能证明 50KB 安全阈值、真实超长上下文或原生 compact 效果；L3 预期事实未写入素材，不能当作召回成功；容量可见路径未获同层实测 |
| `20260912-stage04-parallel/p-samples-summary.md`（P01–P06，含 2026-09-12 用户批准的解释订正） | 决策与裁决行为按合同／决策验证范围保留 | P01 未实际派发、P04 未实际重派、P06 未实际验证正常任务继续；决策样本不单独证明实际派发 |
| `20260912-stage07-host-acceptance/b2-stage04-handover/record.md`（B2） | 实际派发、局部失败重派、依赖顺序、隔离写入、串行合入与恶意材料不执行 | 部分 transcript 与用量已轮转，完整 host-support check 未通过；不能宣称全上下文注入隔离已证明 |
| `20260917-stage07-minperf-chain/record.md`（三项优化短链路，含两轮订正） | 三项优化实际发生，独立核验与质量处理保留 | 合成短链路，不证明完整股票研究提速或 token 节省；实际工具执行时间与模型消息时间分开 |
| main 中期实现文档 §4–5（M2-6 证据组合） | 已批准的链路与 Flash 机制证据组合、预算与停止方法作为既有能力依赖 | 不扩大至跨模型、跨宿主；不恢复原运行已丢失的输入；不把方法闭环说成在线控制器；不重开 M2-6 |

原第 1 组的宿主原生压缩、容量可见实测和安全读取量限制留在原组内，不另增需求或悄悄
改成“已经完成”：未验证的宿主与材料组合不声明支持长上下文验收，按参考值保守执行并
如实记录缺口。本批不承诺补齐所有原生宿主组合；若需认证实际 compact／恢复效果，须另批
最小验证范围与预算，不能仅靠迁移宣称成立。既有证据能覆盖且语义未变的项目不重跑；只有
具体语义变更直接影响某项既有结果时，才列明变更条款、受影响 case 与直接回归。

## 5. 迁移与评审结论

- 阶段 01（迁移与自检，提交 `67d8f11`，交付 `1f8597c`）：相对基点 `c4e5258` 为
  35 文件、+1891/−24（含规划及结果补记）。定向检查（MANIFEST 重算仅 7 个实际变更
  Skill 文件、ruff、文档链接、Skill validate、`git diff --check`）通过；无私有证据
  快照回归（`git archive` 跟踪文件快照，无 `.hetu/`、未设 `HETU_HOST_EVIDENCE`）
  八个定向测试文件 **387 passed**。已经用户人工确认。
- 阶段 02（全面评审与 R1 修复）：规范轴阻断 0；需求轴 P2 问题 R1 经 `2566c5e` 修复，
  复评基点 `1f8597c..ba0fea5` 规范与需求符合性问题均为 0，R1 关闭。本轮实跑
  parallel／capability／reuse／prompt 四个合同文件 **65 passed**；新增回归在内存中
  切到旧规则可检出原缺口，现行规则通过；MANIFEST 内存重算一致。其余选中行为（读取
  保真、文件隔离、裁决／局部失败、共享取数、不重做与交付顺序）未发现其他阻断。
- 阶段 03（本阶段）：固定最终方案备份与本文档整合；最终工程门禁结果以本阶段实际运行
  输出为准，补记于备份分支完成记录（§6）。文件数与测试数为实际结果记录，不作为固定
  验收指标。

## 6. 来源、提交与证据追溯入口

| 项 | 值 |
|---|---|
| 固定功能来源 | `zn_dev=b42b9dda9f57b57002b1509bfa6010d134157e8e` |
| 实际主线基点 | `main=c4e52581e911e395a6e90eaf4cf1ef9af0dca971`（执行时核实与 `origin/main` 一致，无新增语义差异需核对） |
| 权威历史依据回源 | `git show c0c8c53:specs/2026-09-06-stock-analysis-workflow-v1-phase-5-design.md`（原五期设计 §2.3、§2.5.3–5） |
| 产品分支 | `zn_p5e`：`5a5e92b` 规划 → `67d8f11` 迁移 → `1f8597c` 自检补记 → `2566c5e` R1 修复 → `ba0fea5` 评审订正 → `d64cdaf` 复评状态更新 → 本阶段整合提交 |
| 初始方案备份提交 | `zn_p5e_plan@45e1768`（设计与执行计划） |
| 固定最终方案备份提交 | **`zn_p5e_plan@6d5bb4b`**：本主题迁移设计、证据摘要、计划路线图及阶段 01–04 四份文件，逐路径保存自产品提交 `d64cdaf`，追加登记前逐文件 SHA-256 校验一致；该分支不得合入 main |
| 原始行为证据 | 本机 `.hetu/validation/phase-5/`（只读保留，不入库；入口与限制见 §4） |

被本文档替代的过程文档（迁移设计、证据摘要、执行计划目录）已从工作区删除；后续从
固定备份提交 `zn_p5e_plan@6d5bb4b` 按原路径读取，例如
`git show 6d5bb4b:specs/2026-09-24-stock-analysis-workflow-v1-phase-5-execution-design.md`。

## 7. 排除项与文件边界

按已批准设计与阶段 01 执行，未夹带：

- `references/host-tools.md` 的「扩展加载与管理」与「宿主原生接口事实记录（阶段
  07.1b）」不迁入，属原第 5／6 组，留在 zn_dev；`references/checkpoint.md` 的「扩展
  加载摘要」条目不迁入；`references/work-package-result.md` 首段的第三方扩展句不迁入，
  仅迁入正式子任务串行合入新增句。
- parallel 测试的七个历史性能审计函数（`test_impact_map_writeback_is_append_only_new_section`
  及六个 `test_writeback_*`）与其专用常量、helper 不迁入，留在源分支与固定来源，不标记
  通过也不加 skip；新分支工程门禁不依赖 `.hetu/` 私有证据，不通过伪造该文件或无条件
  跳过功能测试解决。
- 未迁入 `scripts/host_acceptance.py`、`capture_host_cli.py`、宿主验收 workflow、
  `hosts.json`、`host_cli` 夹具；未修改十个 CLI 叶子、`references/artifact-contract.md`、
  `scripts/check-run-artifacts.py` 与 `src/`。

main 的 F1/F2、M1/M2 修复与既有断言全部保留；迁移后各 Skill 文件与固定来源的剩余差异
仅为上述排除项及 §3 的 R1 修复语句（已逐文件 diff 核对）。

## 8. 后续去向与停止点

本批已由用户通过 PR #9 合入 main（`751ed53`）。阶段 04 随后经单独授权，依据固定备份和
实际合入差异完成已迁增量清理、保留迁移评审修复，并 rebase 到该 main 提交；交付
`zn_dev@a044bb2`，复评通过。清理前备份 `zn_p5e_dev_bak@8320416` 保留，原第 5–8 组
未迁内容继续在 zn_dev 有效。本批迁移与源分支收尾完成；未推送或强推，远端同步另行授权，
不自动启动后续迁移、认证或性能批次。

本批不授权真实股票分析、模型试跑、生产计量修复、性能采样或宿主认证；新增必需补验须先
提交具体缺口、语义影响、最小动作、全角色全过程成本和停止点，由用户另行批准。未声称
五期整体结束或性能验收达标；迁移分支交付与后续 zn_dev 清理分别报告，不把前者冒称
全部拆分完成。
