# HETU V1 五期扩展管理与计量工具迁移实现

> 文档版本：v1.1（v1.0 阶段 03 交付；v1.1 按用户复评订正五处：加载记录位置、
> 计量对象与未知用量表述、db-export 与 check 输出合同分列、删除已关闭问题遗留表述、
> 验收依据顺序）
> 文档状态：阶段 01 迁移经用户确认，阶段 02 全面评审通过；阶段 03 固定备份与本文档整合
> 已执行；**评审通过、待用户合并 main**，未合入 main
> 创建／修订日期：2026-09-25
> 适用范围：原第 5 组第三方扩展管理、第 6 组计量与宿主验收工具实现的选择性迁移，
> 以及直接相关的第 7 组支持声明／宿主事实记录、第 8 组历史审计测试
> 固定功能来源：`zn_dev=8f9f6743e216f2ca62d668cc5586c17db406278a`
> 实际主线基点：`main=751ed5320e5a9afffc78cc61f4c2542721c52ccb`
> 文档性质：本批迁移要求、实现入口、验收依据与适用限制的统一权威文档；替代本主题的
> 迁移设计、证据摘要与执行计划目录三份过程文档（删除前完整内容固定于备份分支
> `zn_p5t_plan` 提交 `2d773de6b8686504c2f92668dba74c75951c5870`，该分支不得合入 main；
> 回源方式见 §7）
> 验收依据：用户批准的需求和硬约束为最高验收依据；源码、canonical Skill 与当前测试
> 用于证明实际行为，与已批准要求冲突时须报告并订正，不得以实现现状覆盖已批准要求；
> 本文档为上述要求在本批的固化记录

---

## 0. 当前结果

本批把固定来源 `8f9f674` 中已评审的原第 5、6 组实现选择性迁入以 `751ed53` 为基点的
产品分支 `zn_p5t`（原 `codex/phase5-extensions-metering`，仅改名不改历史），为 Skill
补入三类用户可观察的能力：

1. **原第 5 组（第三方扩展管理）**：候选校验／安装、默认禁用、按宿主独立绑定、发布
   版本只读、更新不改旧绑定、绑定仍在时拒绝卸载；硬依赖／完整性／兼容失败关闭，
   扩展正文不可信、不执行附带代码，管理命令不启动研究；实际加载的扩展按五键元数据
   记入 `run.extensions`，采用／排除决定与原因记入 checkpoint 扩展加载摘要与报告附录。
2. **原第 6 组（计量与验收工具）**：`scripts/host_acceptance.py`（SQLite 用量结算：
   请求→message→part→callID 归属，模型请求按 `logical_request_id`＋`attempt_index`
   分别计量并按身份去重，未知用量不作零、无法证明结算时拒绝完整导出，同一只读快照
   读取；db-export 全部验证通过后才发布、写失败只清理本批半成品，check 验收不通过
   仍保存 `passed=false` 与失败原因并非零退出，计时不完整不得通过）、
   `scripts/capture_host_cli.py`（仅声明区提取 flag、保留策展白名单、缺宿主不覆盖
   已有有效捕获并非零退出）、显式离线证据入口 `check --host-evidence` 与手动验收
   workflow。
3. **随迁记录与审计**：第 7 组的宿主原生接口事实记录与支持声明（`hosts.json`、
   host-tools 事实表、native interfaces），第 8 组的七项历史审计测试（缺文件才带理由
   skip，损坏／同名目录不得伪装缺失）。

阶段 01 迁移完成并经用户人工确认（产品提交 `08ea856`）；阶段 02 Spec／Standards 双轴
评审通过，无实现阻断与代码修复；无私有证据回归在无 `.hetu` 的临时副本上复验通过
（§6）。四项迁移前阻断在固定来源已修复并随批迁入（§4）。**本批完成第 5、6 组实现的
迁移与本批评审，不等于第 7 组宿主／模型组合全面认证，也不等于第 8 组性能验收达标**
（§5）。main 的 F1/F2、M1/M2、已迁并行与上下文规则、十个既有 CLI 叶子全部保留；
CLI 新增九个扩展管理叶子（§2）。

## 1. 目标、范围与排除项

本批是已实现功能的选择性迁移，不新增需求组、不另造实现方案、不更改功能要求；剩余
仍按原第 5–8 四组记录编号。目标：用户在产品分支可使用与固定来源相同的行为，正常
工程门禁不依赖私有证据、不启动真实研究。

| 原组号与名称 | 本批处理 | 迁移后的去向 |
|---|---|---|
| 5 第三方扩展管理 | 全量迁入实现与行为测试 | 功能行为完成迁移并经本批评审；非阻断登记项仍留登记（§5） |
| 6 计量与宿主验收工具 | 全量迁入实现与行为测试 | 工具与既修缺陷完成迁移；真实采样与预算校准仍归第 8 组，不自动启动 |
| 7 研究质量与宿主认证 | 只随迁直接相关的支持声明／宿主事实记录 | 深度、多模型、来源切换与完整宿主认证保持原去向，不因工具迁入标为完成 |
| 8 正式性能采样与校准 | 只随迁七项历史审计测试及其最小依赖 | 不自动续开样本，不宣称性能验收通过或预算数值已校准 |

排除项（不得据本批扩大）：不迁旧七阶段计划与集中修复过程文档到产品分支（备份分支
固定，见 §7）；不整文件覆盖长期需求；不迁 AGENTS.md 差异（保留 main 版含来源冻结节）；
`test_phase5_control_contract.py` 的更宽历史预算表增量留源；不删真实 fixtures（它们是
可重复测试输入）；不把私有 `.hetu` 证据、凭据或个人绝对路径数据入库。认证／深度／
多模型／来源切换、真实宿主补验、性能采样与预算数值校准均不启动；Linux/ext4 只保留
未实测披露，不恢复为当前交付任务。M2-6、中期 B1–B6 和既有历史研究继续有效。

## 2. 第 5、6 组行为：完整规则、实现入口与验收依据

下表逐项保留已批准行为的关键条件、例外与禁止项。实现入口相对仓库根；测试文件简称：
extensions＝`tests/product/skill/test_extensions.py`，extension_cli＝
`tests/product/cli/test_extension_cli.py`，artifact＝
`tests/product/skill/test_artifact_checker.py`，host_acc＝
`tests/product/validation/test_host_acceptance.py`，capture＝
`tests/product/validation/test_capture_host_cli.py`，protocol＝
`tests/product/validation/test_host_cli_protocol.py`，native＝
`tests/product/validation/test_host_native_interfaces.py`，adapter＝
`tests/product/validation/test_native_adapter_contract.py`，lock＝
`tests/product/validation/test_phase2_lock_run.py`，command_tree＝
`tests/product/cli/test_command_tree.py`，entrypoints＝
`tests/product/docs/test_product_entrypoints.py`，doc_checks＝
`tests/product/engineering/test_document_checks.py`，skill_pkg＝
`tests/product/skill/test_skill_package.py`。

| 组／行为 | 必须保留的完整规则（条件、例外、禁止项） | 实现入口 | 验收依据 |
|---|---|---|---|
| 5／生命周期 | 候选先校验后安装；安装后默认禁用；默认版本按数值感知排序（数值段按数值比较、前缀与混合 token 有确定次序）；显式绑定版本不因默认变化而变；按宿主独立绑定，禁用一个宿主不影响其他；update 发布新版本、既有绑定保持旧版本；任一宿主绑定仍在时拒绝 uninstall；卸载目标逃逸受管根（绝对路径、`..`、符号链接）时拒绝且不改外部文件与登记表 | `src/hetu_stock/skill/extensions.py`、`src/hetu_stock/cli.py`（`skill extension` 九叶子：list／inspect／validate／install／update／enable／disable／uninstall／context）、`src/hetu_stock/skill/__init__.py`、`src/hetu_stock/skill/work_packages.py`（`third-party` kind） | extensions（含 `test_uninstall_fails_while_another_host_still_binds`、`test_uninstall_refuses_registry_key_escaping_managed_root`、`test_install_rejects_*_without_touching_external_files`、数值感知默认版本组）；extension_cli；fixtures `tests/product/skill/extension_fixtures.py` |
| 5／失败关闭 | 硬依赖缺失、完整性破坏、兼容性不满足时候选整体关闭并给出可定位原因；扩展正文不可信（inspect 只读登记元数据，不展示正文）、不执行附带脚本、拒绝未声明文件与符号链接逃逸；管理命令不启动股票研究；扩展声明新权限必须先取得用户授权 | 同上；`skills/hetu-stock-analysis/references/host-tools.md`「扩展加载与管理」节、`checkpoint.md`、`report-guidance.md`、`work-package-result.md` | extensions 依赖解析／循环／重访环、完整性、越权正反例；command_tree 闭集（十既有叶子＋九扩展叶子，无多余）；entrypoints 文档断言 |
| 5／加载记录 | `run.extensions` 仅记录实际加载扩展的五键元数据（`id`、`version`、`source`、`summary`、`enabled_scope`，均非空字符串），不记录未启用、校验失败或本宿主禁用的候选；采用／排除决定与原因记入 checkpoint 扩展加载摘要与报告扩展附录（被关闭候选只记原因不记正文）；扩展能力不替代 W0–W10 官方工作包；未登记身份不认可 | `skills/hetu-stock-analysis/references/artifact-contract.md`（run.extensions schema）、`checkpoint.md`（扩展加载摘要指令）、`report-guidance.md`（扩展附录）、`skills/hetu-stock-analysis/scripts/check-run-artifacts.py` | artifact（run.extensions 闭 schema 六变异＋正反例） |
| 6／计量归属 | 请求→message→part→callID 逐级归属，工具按 callID 关联；模型请求按 `(logical_request_id, attempt_index)` 分别计量（重试分开计量），同一 attempt 的重复合法快照按身份去重、同 attempt 两行结算值冲突时发布前拒绝；未知用量不得当作零（保持未知），无法证明结算时拒绝完整导出；同一只读快照上结算，历史账目不重算 | `scripts/host_acceptance.py` | host_acc（4566 行合成数据库正反例）；fixture `tests/product/fixtures/host_acceptance/synthetic-verifier-usage.jsonl` |
| 6／发布与清理 | db-export 全部验证通过后才发布（任一会话验证失败则不写任何文件），写失败只清理本批创建的半成品，既有产物不覆盖（重跑抛可定位拒绝）；check 验收不通过时仍把 `passed=false` 结论与失败原因保存到新输出（不能静默跳过、不把 skip 算通过），并非零退出即弃；计时不完整（含 delivered_at 缺失且无具体 gap）不得通过 | `scripts/host_acceptance.py`（db-export/check）、`scripts/phase2_lock_run.py`（交付定位输出） | host_acc 相应用例；lock 正本路径断言 |
| 6／flag 提取 | 仅从帮助文本声明区提取 flag；`accepted_flags`／`checked_absent` 策展名单保留；宿主二进制缺失时保护已有有效捕获（不改写、非零退出），首次无记录仍记录 unavailable | `scripts/capture_host_cli.py`、`skills/hetu-stock-analysis/hosts.json` | capture；protocol；四宿主夹具离线核对（`tests/product/fixtures/host_cli/`：claude/codex/opencode/zcode＋native-interfaces） |
| 6/7／离线入口 | `check.sh` 支持显式 `HETU_HOST_EVIDENCE` 证据入口，默认不触发宿主验收；验收 workflow 仅手动触发；支持事实可查；不把工具通过当组合认证 | `scripts/check.sh`、`.github/workflows/host-acceptance.yml`、`README.md`、`docs/agent-skill-usage.md` | host_acc `test_071a_check_sh_*`（默认不触发／带证据选择通过）；doc_checks（workflow 手动触发／secrets／env 注入）；entrypoints |
| 7／事实记录 | 宿主原生接口事实表与字段白名单如实登记；协议测试仅只读 `--version`／`--help` | `skills/hetu-stock-analysis/references/host-tools.md`（事实表）、`hosts.json` | native；adapter；protocol；skill_pkg（hosts.json 字段白名单与 MANIFEST 哈希覆盖） |
| 8／历史审计 | 七项 writeback 审计以 `.hetu/validation/phase-5/20260909-stage02-calibration/impact-map.md` 为输入；仅证据完全缺失时带理由 skip（skip 不代表性能验收通过）；路径存在但损坏、不可读或为同名目录时照常失败，不得伪装缺失 | `tests/product/skill/test_phase5_parallel_contract.py`（七项审计＋`_writeback_section` 等 helper） | 本机证据存在时七项全部运行通过；干净克隆缺失时按 skipif 跳过；损坏／目录负例实测失败（§6） |

全部迁移以固定源的最终评审语义为准；对 main 的 F1/F2、M1/M2、已迁并行与上下文规则、
十个既有 CLI 叶子逐项负向差异核对，未发现删弱（§6）。完成标准：第 5/6 组全量批准
行为在产品分支可观察、可复核，main 既有行为不回退，支持声明与证据一致，干净副本可
验证，正式文档无删弱条件。

## 3. 第 7 组随迁声明与第 8 组历史审计的准确范围

- **随迁的只是「声明与事实记录」**：宿主原生接口事实（native interfaces 快照、
  hosts.json 白名单）、离线证据入口说明与手动 workflow 文档化。**不构成任何宿主／
  模型组合的正式支持认证**：ZCode 既有组合质量门槛与计量恢复结论维持 2026-09-14/15
  口径不变；Codex、OpenCode、Claude Code 仅为功能层验收（2026-09-13），完整研究
  `UNVERIFIED`。
- **Claude `--permission-prompt-tool`**：因描述续行泄漏从 `accepted_flags` 移除
  （§4 第 4 项）；**支持性未获证明，不在 accepted_flags，也不在 checked_absent**，
  不推断宿主支持或支持性须未来真实宿主证据，本批不补验。
- **随迁的第 8 组只是七项历史审计测试**：审计对象是 2026-09-09 stage02 校准
  impact-map 的阶段 04 实现回写（追加式、四列、引用真实测试名、待定数值不宣称收益），
  **不是性能采样**。三条性能验收线均未正式通过；并行收益／性能验证增量仍留源分支；
  预算数值为参考值待校准。缺失跳过不代表性能验收通过。

## 4. 四项迁移前阻断及最终修复结论

固定来源全面评审（2026-09-25）确认的 4 项迁移前阻断，均属原第 5、6 组组内缺陷，经
用户批准（含两项取舍：缺宿主保留捕获时非零退出；claude 夹具显式移除
`--permission-prompt-tool`）在固定来源修复（实现提交 `8e0177f`，先红后绿，定向回归
282 passed／7 skipped），随后随本批迁入产品分支并经阶段 02 复核。**四项均已关闭，
不是当前未修阻断**：

| # | 已证场景（修复前） | 修复行为（迁入后） | 验证 |
|---|---|---|---|
| 1 | 登记表键被篡改为外部路径后 uninstall 递归删除外部目录 | 删除与写回前校验 ID 形态与受管边界，非法抛 ExtensionError，外部文件与登记表字节不变 | extensions 越界负例＋正常卸载／绑定仍在拒绝回归 |
| 2 | `delivered_at=None` 且无具体 gap 时 check 仍 passed | complete=False 且无具体 gap 时追加可定位失败，指明缺失时点；不补造时间、不以零代未知 | host_acc 缺交付时点负例（probe 与非 probe 同语义） |
| 3 | 宿主二进制缺失时刷新把已捕获夹具改写为 unavailable 且 exit 0 | 已有有效捕获（status=captured 且含 help_text）时不写回、明示未更新并非零退出；首次无记录仍记录 unavailable | capture tmp 夹具＋mock 正反例 |
| 4 | 描述续行文本被当选项声明提取（`--permission-prompt-tool` 即由此泄漏） | 声明段占位符抹除后含非 flag 文本即判续行跳过 | capture 合成续行负例；三宿主离线对照：codex 31 项、opencode 25 项零变化，claude 76 项仅少 `--permission-prompt-tool` |

遗留未确定事项仅一项：`--permission-prompt-tool` 是否受宿主支持（§3）。

## 5. 支持范围、能力限制与非阻断登记项

**支持范围**：单只中国 A 股 `security`；Python 3.11/3.12；宿主验收按 README 既有口径
（ZCode standard×public 组合质量门槛已验收；计量维度 2026-09-15 口径恢复；quick/deep
有证据未达组合门槛；其余三宿主功能层验收、完整研究 UNVERIFIED）。

**有效历史证据**（不因本批重验）：M2-6、中期 B1–B6、五期前半/中期/下一批已合入
main 的验收，以及固定来源集中修复批的最终门禁（1592 passed、6 skipped，均为带理由
宿主可用性 skip，exit 0）继续有效；本批只做 §6 所列新验证。

**能力限制与非阻断登记项**（保留披露，不因迁移变成「已验收」，不夹带修复）：

- 完整宿主／模型组合认证未启动；三条性能验收线未通过；预算数值待校准。
- Linux/ext4 仅保留未实测边界披露，不是当前交付任务。
- remainder §4 第 8 项仍保留的非阻断登记：db-delivery 重跑抛未捕获
  FileExistsError、NULL `started_at` 行静默掉出导出窗口、扩展更新中断重试等边缘问题、
  `HETU_HOST_TOKEN` 无消费方、过程 SHA 锚定、8 处死代码／死数据，及 E3A、缺工具刺激、
  E1 归因、ZCode schema／环境限制等既有登记项（缺交付时点的 check 误通过已随 §4
  第 2 项修复关闭，不列为遗留）。
- **正常工程门禁为 `mypy src`**；脚本 mypy 存在 55 项既存诊断（与修复前配平、无新增），
  不把全脚本类型清零设为迁移条件，不声称全脚本 mypy 通过。

## 6. 迁移、评审与无私有证据验证结果

**阶段 01（产品提交 `08ea856`）**：62 个差异路径按方案逐路径处理——全新文件整份
提取 19 个（逐文件 SHA-256 与固定源一致）、混合文件取源版本 19 个（main→源 diff 逐块
核读，确认均为本批批准增量）、MANIFEST 由脚本重生成（与固定源逐字节一致）、块级整合
6 个（README、使用文档、三份规格、长期需求——保留 main 全文仅加注记）、不迁产品
17 个路径（§1、§7）。定向套件 **709 passed、7 skipped、0 failed**（105.9s）；skip 均为
带理由宿主环境条件（zcode 夹具 unavailable／未安装、已捕获宿主不适用、opencode/zcode
本机漂移检查跳过）。七项历史审计因本机 `.hetu` 存在全部实际运行通过。
`update_skill_manifest.py` 重生成无差异；`check_docs.py` 通过（28 份文档）；
`hetu-stock skill validate` 通过；`git diff --check` 通过。

**阶段 02（2026-09-25，评审通过）**：Spec／Standards 双轴均通过，无实现阻断及代码
修复；固定源与产品的 src/scripts/Skill/fixtures 及随迁测试逐文件一致，卸载边界、
逐行结算后去重、工具属主、只读快照、导出清理、计时失败关闭、捕获保护均未丢失；
main 既有 AGENTS、orchestration、recovery、reuse/control 测试保持，十个既有 CLI 叶子
未删、新增九个扩展管理叶子。文档订正四项记录错误（混合文件 22→19、旧计划文件 8→7、
环境 skip 6→7、备份旧 SHA 82fb776→32e8150；总分类 19+19+1+6+17=62）。

**无私有证据回归**：从 `git archive 08ea856` 提取无 `.hetu` 的临时源码副本（非
worktree，独立 Git 索引仅为满足合同测试中的 git diff 调用，未改任何引用），断言
`hetu_stock.__file__` 位于副本后运行定向套件：**703 passed、13 skipped、exit 0、
96.23 秒**（13 skip＝7 历史审计缺证据＋6 宿主环境；与阶段 01 的差异均为环境条件，
未改夹具或验收边界）。负例：在临时副本构造损坏文本与同名目录两态伪证据，七项
writeback 审计各 **7 failed、29 deselected、exit 1**，证明不以 skip 隐藏损坏；原始
`.hetu` 未触碰。

## 7. 来源、备份与回源入口

| 项 | 值 |
|---|---|
| 实现固定来源 | `zn_dev` @ `8f9f6743e216f2ca62d668cc5586c17db406278a` |
| 产品基点与分支 | main `751ed5320e5a9afffc78cc61f4c2542721c52ccb`；`zn_p5t`（原 `codex/phase5-extensions-metering`） |
| 阶段 01 前置备份 | `zn_p5t_plan` @ `32e815017e5631fd213dd561794150c391271490` |
| **删除前固定备份提交** | `zn_p5t_plan` @ `2d773de6b8686504c2f92668dba74c75951c5870`（正式文档引用此固定值，不引用会前进的分支名） |
| 固定备份内容 | 本批迁移设计、证据摘要（阶段 01–02 终态）、路线图及五阶段计划（同产品 `380054c` 逐字节一致）；固定来源的集中修复设计、修复计划、旧七阶段计划与源计划索引（`zn-dev-snapshot-8f9f674/` 15 文件，与来源 git blob 逐字节一致）；`MIGRATION-PROVENANCE.md` 去向／来源与 `SHA256SUMS.txt` 逐文件哈希（25 条全部回读核对通过） |
| 仓库外持久副本 | `~/trading/HETU-migration-handoff/2026-09-25-extensions-metering/`（阶段 01 前置备份时点，含 `SHA256SUMS.txt`） |

**回源方式**：过程文档查 `git show 2d773de:<路径>` 或检出 `zn_p5t_plan`；固定来源
原位读取 `git show 8f9f674:<来源路径>`（与备份快照逐字节一致）。备份分支不得合入
main。阶段 04 用户合并与阶段 05 源分支去重与 rebase 按 `2d773de` 中阶段 04/05 计划
执行，后者须单独授权；本批结束不推送、不自动合并。

## 8. 后续去向与停止点

- 本文档交付后阶段 03 完成，**停止交用户验收与合并 main**；合并前状态统一为
  「评审通过、待用户合并 main」。
- 第 7 组完整认证、第 8 组性能采样与预算校准维持原去向，须另行批准；不因本批工具
  迁入自动启动。旧性能批次不自动续开；任何重跑遵守既有结果有效性与分析成本门禁。
- 已删除过程文档的现行入口：本节与 §7；不新增对已删路径的引用。
