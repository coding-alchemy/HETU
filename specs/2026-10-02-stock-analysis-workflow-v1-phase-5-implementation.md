# HETU 个股分析工作流 V1 五期实现：控制、协作、维护与验收

> 文档版本：V1 五期实现 v1.4
> 文档状态：功能已交付，第 5–7 组限定验收已收口；第 8 组性能数值未验收，五期整体未完成
> 创建／修订日期：2026-10-03
> 批准依据：各批次已批准要求、评审结论和知情取舍
> 适用范围：五期 F1/F2、原第 1–8 组的需求、实现、验收边界与停止点
> 四期基线：[V1 四期实现](2026-09-01-stock-analysis-workflow-v1-phase-4-implementation.md)
> 长期需求：[V1 长期需求](stock-analysis-workflow-v1-requirements.md)
> 文档性质：五期需求、实现行为、验收结果与限制的统一说明
> 验收依据：用户最新批准要求与硬约束优先；源码、canonical Skill、测试和真实证据用于证明行为，不以实现现状覆盖要求

章节入口：[当前结果](#phase5-status) · [需求对照](#phase5-requirements) · [安装与旧成果](#phase5-foundation) · [控制与复用](#phase5-middle) · [上下文与并行](#phase5-execution) · [扩展管理](#phase5-extensions) · [计量工具](#phase5-metering) · [质量认证](#phase5-certification) · [预算与性能](#phase5-budget) · [验证与证据](#phase5-history) · [限制](#phase5-limitations)

<a id="phase5-status"></a>

## 0. 当前结果与停止点

五期已交付安装维护、旧成果处理、任务控制恢复、资料复用追溯、长材料管理、正式子任务与汇合、三项减少重复工作的优化、扩展管理和计量工具。实现交付、受控行为证明、正式认证与性能达标分别判定。

**五期整体未完成。2026-10-02 用户决定不再投入性能验收。** 三条性能线、全流程取样和三档实测校准的要求与缺口保留；不新增采样、配对、校准、补探针或 X4 补核验，不重复请求延期批准。第 5–7 组有效结论不重开，长期 V1 发布要求仍有效。

### 0.1 逐组现行状态与未完成事实（2026-10-02）

| 原组 | 现行结果 | 保留边界与详细依据 |
|---|---|---|
| 第 1–4 组：控制、上下文、复用、并行与三项优化 | M1/M2 经 PR #8、长材料／正式并行及三项优化经 PR #9 合入 main；受控行为证据有效 | 原生压缩、容量可见和单批安全读取量未证；真实并行与复用收益归第 8 组。行为、证据及限制见 §3–§4 |
| 第 5 组：扩展管理 | PR #10 交付、PR #11 修复更新重试；四宿主管理主流程有效；2026-09-29 用户限定接受 | 安全停止有证，“因管理 CLI 缺失而拒绝”未直接证明，不再补探针；非 ZCode 延迟结果仅备案。见 §5 |
| 第 6 组：计量工具 | PR #10 交付、PR #11 修复双表 NULL 时间；db durable 通道可用；2026-09-30 用户限定原五运行闭合 | S4/S6/S7/G67-R1/G67-R2 仅本批闭合；原失败和 check 不改写，历史第三模型 R1 不作性能基线。见 §6 |
| 第 7 组：质量与认证 | 历史 ZCode standard×public 10/10、五槽／三域限定验收及 688048.SH 专项有效；本批仅 ZCode×GLM-5.3-Flash×standard×public 按 4/5＝80% 限定达标 | 含明示个案例外，G67-R1 失败留分母；quick 6 次、deep 1 次未达每组合 10 次，完整组合认证未闭合。其他宿主完整研究 0 次，备案以后另验，不阻断五期。见 §7 |
| 第 8 组：预算与性能（长期 §2.7／§3.9） | 真实并行重叠及复用行为已证；三档默认于 2026-10-01 批准并进入 Skill；N7＋X1＋X4 获批范围执行完毕 | 三条性能线、全流程取样及三档实测校准未完成；X1/X4 不构成合格配对，启动额度用尽，X2/X3 未批。具体停止点与资格见 §8.5 |

本表不因其他宿主曾出现在历史记录而增加本期工作，也不降低 ZCode 门槛。阶段 05 已于
2026-10-02 完成汇总、交付并待用户验收（0 新运行、0 实现改动），依据见
[验收证据 §2、§6](validation/2026-10-03-phase-5-acceptance-evidence.md)。提交、推送或 main 集成按用户具体指令处理。

<a id="phase5-requirements"></a>

## 1. 需求范围与有效性

### 1.1 共同约束

1. 主 Agent 保持研究控制权，沿用 W0–W10、独立核验、点时、授权、安全、深度与质量要求；代码只提供原子确定性工具，不恢复中央工作流、状态机、代码语义门禁或报告业务生成器。
2. 已通过结果默认有效。提交、分支、版本、模型标识、哈希、日期与 `as_of` 本身不使既有结果失效；新时点产生新结果，旧结果仍适用于原时点。只有可定位的具体语义变化直接影响原验收依据，才列明条款、case、直接证据并做最小重验。
3. 整批或已通过项重跑须单独说明直接影响、预计股票分析次数及成本并获批；历史账目、失败、原始 check、知情接受和未采用版本均保留，不用新导出覆盖旧结论。
4. 五期适用验证只要求 ZCode；Codex、Claude Code、OpenCode 的有效结果与未证明项备案，以后另验。该范围调整不放宽 V1 发布 P0-9：所有宣称支持的组合仍须有脱敏、可复核证据。Linux/ext4 未实测只披露平台事实，不属于五期或长期补验要求。
5. 需求与硬约束、静态映射、机械检查、真实行为、限定接受各自有效，不能互相替代。缺证据时报告具体未成立项，不据局部能力缺口否定独立已通过成果。
6. 不扩大为估值增强、完整监控、全脚本类型清零、死代码集中清理、新调度框架、模型路由或自动计费。未授权新增真实运行，不自动续用旧批次或未用额度。

### 1.2 后续变更与运行授权边界

真实会话、探针或股票分析仍须逐卡批准场景、宿主／模型、启动次数（含重试）、单次／总输入输出预算、证据目录、恢复、检查点、停止条件及失败处置；预算不能被当作可强制硬上限，偏离如实登记。后续多公司成本门禁按 AGENTS.md 执行，不自动沿用旧批次的 Flash 选择，不自行切模型。

实现修改须有直接缺陷证据并按适用门禁取得授权，不重复或弱化已有行为、不覆盖未提交改动。原始 .hetu、凭据和个人路径不进入产品增量。证据不可回源时报告具体缺失，不以新运行伪补；能力补齐另行授权，不自动重开第 5 组因果缺口、第 6 组历史缺口、D1／S2／CQ 等暂缓项或范围外认证。

### 1.3 原五期条款对照

沿用原五期条款编号，与长期需求现有编号分开。下表负责条款去向，逐组状态见 §0.1；
细则、证据和未完成标准见对应章节。

| 五期条款 | 现行章节 | 已有证据与保留缺口 |
|---|---|---|
| 2.1 预算、停止与进度（时间目标暂非 P0） | §3、§8 | C01–C10、同类重试、七类停止与超时合同已交付；预算方法及离线验证闭环，默认已批准，实测校准与性能未完成 |
| 2.2 自然语言取消、调整与恢复（M1） | §3 | B1 §1–2：TaskStop、迟到未采用、候选恢复与既有文件不变；功能复评闭环，2026-09-21 起经 PR #8 交付 |
| 2.3 能力预检、长上下文与压缩恢复 | §4 | L1–L3 合同层通过；全面评审及 R1 复评通过，2026-09-24 起规则经 PR #9 合入 main；原生压缩、容量可见和单批安全读取量未证 |
| 2.4 正式并行拆分与隔离 | §4 | P01–P06 合同／受控决策样本；PR #9 交付，含共享取数优化；真实并行收益仍待第 8 组 |
| 2.5 汇合、冲突裁决与局部失败 | §4 | P03/P04/P06 与 B2 §1–3；PR #9 交付，含汇合不重做与交付先于锁后评分 |
| 2.6 重试继承、默认复用与采用记录（M2） | §3 | R01–R03（R03 允许范围与复制失败由 B1 §3／R04 补足）；M2-6 组合证据闭环，原输入不可追溯限制保留；2026-09-21 起经 PR #8 交付，复用收益未测 |
| 2.7 真实并行收益 | §8.3、§8.5 | ≥3 域真实重叠已证；中位提速 ≥20%、质量不降及调用约束未正式验收 |
| 3.1 安装维护（F1） | §2、§9.1 | 已验收并交付；G7-I1 安装／发现／按需读取已接受（§7.1），不新增平台补验 |
| 3.2 第三方扩展管理 | §5 | PR #10 交付；四宿主管理主流程有效，第 5 组限定接受，缺 CLI 拒绝因果未证 |
| 3.3 正式宿主支持组合认证 | §7 | 本批目标组合按 4/5＝80% 与明示例外收口；其他组合／宿主以后另验，不替代完整组合认证 |
| 3.4 真实宿主验收入口 | §6 | host_acceptance、capture_host_cli 与 CI workflow 等经 PR #10（`3acf981`）交付；原五运行限定闭合 |
| 3.5 旧成果读取与导出（F2） | §2、§9.1 | 已验收并交付 |
| 3.6 研究质量门槛 | §7 | 历史 standard×public 10/10 有效；本批 4/5＝80%、故障／阻断覆盖后置获批，长期完整矩阵不变 |
| 3.7 深度、多模型与来源切换 | §7、[专项验收](#phase5-depth-model-source) | 五槽／三域独立技术评审通过、用户限定终验完成；S2/CD/S6 直接计入，A1 与历史 R1 第二启限定计入；三域切换经锁后评分和独立评审核对，R1 原 check 失败及 unconsumed_tail 保留 |
| 3.8 688048.SH 深度回归 | §7、§10.3 | 既有结论有效，未出现语义不外推 |
| 3.9 全流程提速 | §8.3、§8.5 | 三线均未正式通过；合格配对 0/3、0/1、0/1，复用收益未测 |

<a id="phase5-foundation"></a>

## 2. 安装维护与旧成果（前半 F1/F2）

这里的 F1/F2 指安装维护与旧成果处理；§5.3／§6.3 的“定向修复 F1/F2”分别指扩展更新恢复和 NULL 时间拒绝，两套历史简称不得混淆。

前半交付的 CLI 为 10 个叶子（6 个 skill＋4 个 helper），canonical Skill 与四期基点 `73f28f6` 无差异；这两项只描述当时前半交付面。后续扩展管理新增九叶子，现行命令树为原十叶子加九扩展叶子，不能把历史十叶子结论当作当前全树要求。

### 2.1 完整需求与安全边界

#### 2.1.1 F1 用户行为与正常路径

1. 安装经 `install.sh --host <host>` 或 `skill install`；已有 Skill 更新显式 `--force`。
   覆盖安装为“准备完整新版 → 校验 → 原子交换目录 → 确认结果并保留旧版备份”。
2. 辅助 Python 环境按版本独立创建，不在旧环境原地升级；启动器为受管符号链接，Skill 发布与
   启动器切换分别原子。
3. `skill status` 只读报告安装状态；`skill diagnose` 给出完整性、备份、启动器归属、辅助
   环境与下一动作；不联网、不自动修复、不输出凭据或授权材料，路径展示脱敏。
4. `skill rollback` 回滚到一份完整备份并保留被换下版本；默认安装的辅助环境／启动器组合
   随回滚恢复到该备份记录的组合，无法唯一判断时不猜选并如实警告。
5. `skill uninstall` 先列出处理范围，经 `--yes` 确认后才删除；`--remove-env` 仅在无其他
   宿主使用该环境时删除。
6. 正常研究不需要调用维护命令；维护不启动研究，不例行重装或联网升级。

#### 2.1.2 F1 失败与安全条件

1. 复制、清单或结构校验失败仅清理本次暂存，旧目录保持可用；安装器拒绝包内符号链接、越界
   路径、缺失文件和清单不一致，不因强制更新放宽校验。
2. 原子交换能力或文件系统不支持时，在触碰旧目标前失败并返回具体原因；不降级为先删后复制。
3. 交换前写入并刷盘最小安装记录；失败时交换回旧包；异常中断后下次维护先按记录与实际目录
   恢复，不能盲删暂存；恢复也失败时保留现场、事务不记为完成并报告精确修复动作，禁止声称
   回滚成功。
4. 新环境准备、依赖安装或 CLI 自检失败时，旧环境与旧 Skill 保持可用；跨目录整套维护不是
   单个原子操作，后续步骤失败按安装记录恢复已切换部分。
5. 卸载只删除明确受管的 Skill、受管启动器和（可选且未被共享的）辅助环境；其他宿主仍在
   使用的共享环境与共享启动器保留；研究、旧报告、授权配置和非受管文件保留；拒绝符号链接
   目标；不把宿主整个配置目录当作卸载目标。仅凭路径形状不构成受管依据，环境删除须以维护
   记录归属为准；持久登记的宿主引用（`hosts/*.json`）同样计入共享核对。
6. 由旧版安装器创建的 `hetu-stock/venv` 布局被安全识别并保留为上一可用组合，可直接
   `--force` 升级。

#### 2.1.3 F2 用户行为与正常路径

1. `helper archive-inspect --source <旧任务目录>` 只读检查旧格式（schema 3）目录：请求、
   证据与主张、论点字段、阶段尝试、原采用指针、用户决定和已有状态按原值逐字段呈现，状态
   标注为“原记录”，不解释为当前任务可恢复。
2. `helper archive-export --source <旧目录> --output <新目录>` 在新目录生成可读索引，并按
   用户授权复制原记录及相关附件；保留原文件，索引给出源文件及字段定位。
3. 用户可用自然语言要求查看旧成果，由 Agent 调用同一组命令；新研究入口不读取旧状态推进
   研究，不重新导入旧 `RunState`、`StageResult` 或状态机，不启动旧执行器。

#### 2.1.4 F2 失败与安全条件

1. 未知版本或无法映射字段保留原文或原文件，标明未解释范围；不为统一显示丢字段、猜结论
   或重建旧报告；损坏记录披露具体损坏范围，原件保留可取回。
2. 拒绝路径穿越与符号链接越界：读取任何文件前按解析后的实际路径核对仍在指定源目录内；
   越界项记入问题清单，外部内容不进入读取结果；不执行文件中的命令、HTML 或外部下载。
3. 导出不覆盖既有目录，不扩大授权材料的保存或传播范围；对不能复制的材料保留许可允许的
   定位与原因，不声称完成无损全量导出；全部记录受限时仍生成可读索引并登记未导出范围。
4. 原记录含已支持 JSON 秘密字段时，不复制到可读索引或导出包并登记脱敏与未导出范围；
   按解析后的实际 JSON 内容识别，符号链接别名、目录位置或文件后缀不能绕过；索引及诊断
   说明只使用脱敏显示数据；源文件字节保持不变。

#### 2.1.5 支持声明边界

- 平台：安装维护已验证范围为 macOS/APFS（真实交换、并发串行化、中断恢复及不支持交换负例
  均实测）。Linux 共用实现及不支持时安全失败机制保留，但不宣称已验证；按用户
  2026-09-27 决定，Linux/ext4 实测不属于五期或长期需求，不列后半待办。
- 脱敏：秘密过滤限已支持的 JSON 秘密字段，不承诺任意自然语言秘密识别。
- 旧成果：仅明确映射 `schema_version: 3`；未知版本与无法映射字段保留原样并标明范围。
- 宿主：ZCode 安装目标（`--host zcode`）由安装器支持；前半支持该目标不独立证明宿主认证；后续安装／发现证据 G7-I1 与本批限定认证见 §7.1。

### 2.2 安装、恢复、诊断与导出实现

安装维护入口为 `src/hetu_stock/skill/installer.py`、`install.sh`、`src/hetu_stock/cli.py`，
验证入口为 `test_installer.py`、`test_install_script.py`、`test_skill_cli.py`。
旧成果入口为 `src/hetu_stock/helpers/archive.py`，验证入口为 `tests/helpers/test_archive.py`
及 legacy-run 夹具。平台、schema 与脱敏范围见 §2.1.5，历史验收见 §9.1。

#### 2.2.1 安装、更新与故障恢复

- 一次维护在目标所在文件系统的受管维护区（宿主 Skill 发现目录之外，按规范化目标定位）
  准备完整包；同一目标的安装、回滚、卸载共用锁；未确认同文件系统条件时在交换前失败。
- 首次安装以同文件系统目录改名发布；覆盖安装使用原子目录交换（macOS
  `renameatx_np(RENAME_SWAP)`，Linux 对应 `renameat2(RENAME_EXCHANGE)`）；交换后旧包完整
  保留为可回滚备份。不把“旧目录改名再改名”称作原子替换。
- 交换前写入并刷盘最小事务记录（目标、暂存、新旧包摘要、状态）；交换后校验目标并完成
  记录。事务状态覆盖 prepared／exchanged／finalized／rolled_back；回滚后段（启动器切换）
  失败时恢复交换前一致组合并记 rolled_back；恢复本身也可能被中断，恢复尝试前先把事务记为
  `restoring`（含交换前摘要与备份原组合记录），后续维护按摘要完成正确恢复，`restoring`
  现场不列为普通可用备份；任何维护先结算中断事务再选择备份与确认组合，失败现场不会被
  误当完整备份而“成功”回滚到错误版本。
- 辅助环境按版本独立创建并在最终保留路径完成依赖安装与 CLI 自检；启动器用临时链接原子
  替换，拒绝覆盖非 HETU 受管文件（回滚交换前核对启动器归属，非受管对象拒绝且不改动现状）。
  后续步骤失败按安装记录恢复已切换部分；完成后保留上一可用组合供回滚。
- 目标路径由既有默认解析：Codex `$CODEX_HOME/skills`（缺省 `~/.codex/skills`）、Claude Code
  `~/.claude/skills`、OpenCode `$XDG_CONFIG_HOME/opencode/skills`（缺省
  `~/.config/opencode/skills`）、ZCode `~/.zcode/skills`，末级均为 `hetu-stock-analysis`。
- 组合记录（备份的 `combo.json`、受管根 `installation.json`、持久宿主引用 `hosts/*.json`）
  判定受管归属与共享；卸载共享核对结合持久登记的实际安装路径。该记录只描述安装事务，不
  记录证券、工作包进度或研究下一动作。

#### 2.2.2 诊断、回滚与卸载

状态和诊断展示实际安装位置的脱敏表示、包完整性、可用备份、启动器归属、辅助能力与错误
原因；完整性检查不自动联网或修复，诊断给出下一动作，不输出环境变量全集、凭据或授权材料。
回滚选择一份完整备份，走同一暂存校验与交换流程，保留被换下版本。卸载先列出所选宿主
Skill、明确受管启动器、辅助环境的处理范围，再执行用户确认的删除。

#### 2.2.3 旧成果读取与导出

读取器将指定目录的 `state.json`、`stage_results/*.json` 和已有报告按普通数据处理，首个
明确映射为 schema 3。导出在新目录生成可读索引（字段定位、脱敏与未导出范围、读取范围
诊断），按授权复制原记录及附件；索引与诊断说明只使用经 `_strip_secrets` 的显示数据。
两个 archive 命令为懒加载；安装器仅依赖既有包校验；不恢复 workflow、report 或 legacy
执行入口。

<a id="phase5-middle"></a>

## 3. 任务控制恢复与资料复用（M1/M2）

M1-1～M1-5、M2-1～M2-7 十二条功能要求在已批准范围内闭环。正确性证明限已验证的 ZCode 桌面端交互协调方式；不把通用规则、schema 校验或受控样本外推为所有宿主实际行为。

### 3.1 十二条要求、实现入口与验收边界

编号用于验收对账；取消、调整、恢复与复用的完整规则如下。实现入口相对 `skills/hetu-stock-analysis/`；“静态合同”指 prompt 文本断言，
“机械”指检查器与测试回归，二者不单独证明真实行为；“行为证据”均为本机 `.hetu/` 原始
记录（见 §9.6），各有限定范围。

| 编号 | 必须保留的行为 | 实现入口 | 验收依据 | 适用边界 |
|---|---|---|---|---|
| M1-1 取消／迟到 | 取消后停止新研究调用与子任务；迟到返回保留未采用，未经恢复不合入 | `references/recovery.md`「取消与迟到返回」、`references/orchestration.md` 停止条件、`references/host-tools.md` 能力预检 | 静态合同 13 断言之一；行为：原生 TaskStop 实际接收、取消后零新调用、迟到产物全部登记未采用（B1 §1）、C03 合成判定 | 已声明 ZCode 桌面端协调方式；其他宿主未证；合成判定不单独证明宿主实际接收取消 |
| M1-2 调整／提前交付 | 区分新增、保留、冲突，只调整剩余工作；提前交付保留两轮自检与缺口披露 | `references/recovery.md`「中途调整与提前交付」、W10 自检条目、`references/checkpoint.md` 用户决定 | 静态合同；C04/C05/C06 合成判定（保留无冲突成果、新增按未完成、提前交付两轮自检含独立核对） | 受控合成证据范围，不含真实会话提前交付 |
| M1-3 候选定位 | 在当前或用户指定且获准访问的研究根定位；按会话关联、公司／代码、日期、深度筛选候选，不全量加载旧研究；真歧义向用户展示可辨认信息，不索要内部 ID；记录缺失说明限制，不猜选 | `references/recovery.md`「恢复候选筛选」 | 静态合同；B1 §2 两候选定位、未全量读（旧报告 mtime 不变） | 无歧义路径已证；真实歧义交互仅有反事实处理说明（C07） |
| M1-4 恢复执行 | 核对主体、时点、模式、授权与最新要求；只继续未完成及直接受影响工作，保留失败历史 | `references/recovery.md`「同一任务恢复」、`references/checkpoint.md` | 静态合同；B1 §2 恢复点核对、仅新增文件未动既有文件、W5 独立复算一致；阶段 05 作者 stopped 状态续接为同任务恢复真实样本 | 已声明方式内 |
| M1-5 身份与继承 | 同一请求稳定任务标识；跨任务继承说明父任务、原因与范围；checkpoint／manifest／owner 各司其职 | W0、`references/checkpoint.md`、`references/artifact-contract.md`、`references/report-guidance.md`、`scripts/check-run-artifacts.py` | 机械：execution 14 项回归（有效关系、旧记录兼容、披露、未知键拒绝、五键、时间、相对定位等）；行为：B1 恢复记录与产物一致 | 字符串字段不证明关系真实（机械边界已声明）；不事后从最终报告补造关系 |
| M2-1 开关 | `reuse_previous_task_data` 默认 true；“不用旧任务数据”映射 false；访问旧资料前明确实际值及来源 | W0「任务标识与复用开关」、`references/recovery.md` 复用节、SKILL.md 正常入口 | 静态合同（默认与映射、正常入口加载、W0 触发）；R01（默认 true 实际复制）／R02（显式 false 零读取）请求与读取次序；阶段 03 作者 false 映射原生记录 | 原 R02 的 manifest 形态非正式 run/artifacts 合同，不作 schema 证据 |
| M2-2 适用性 | 核对主体、期间、口径、原始来源、点时、授权与输入方法；已有适用材料直接使用，仅处理受影响部分 | `references/recovery.md` 复用节 | 静态合同；R01 仅补新增不整份重下；R04c／B1 §3 主体不符零采用 | 无 |
| M2-3 独立副本 | 只复制实际采用且许可允许的材料；复用解析或计算结果时同时保存必要合法输入与依赖；读取、核验和报告引用均使用当前任务副本，旧目录不可访问时仍可核验 | `references/recovery.md` 复用节、`references/artifact-contract.md` 复制范围 | 静态合同；R01 旧目录改名隔离后三份本地副本可读且哈希闭合；R04a 副本哈希与来源一致 | 无 |
| M2-4 许可与失败 | 受限材料只保留允许的定位和限制；中断或校验失败如实登记不记成功；重试另留版本 | `references/recovery.md` 复用节、`references/artifact-contract.md` | 静态合同；R04a 受限不复制、R04b 中断不记成功重试另立文件、B1 §3 四故障实际动作 | 无 |
| M2-5 关闭与不回退 | false 时不查找、读取或采用旧任务原始／派生数据与上下文旧结论；新来源失败走合法替代或留缺口 | `references/recovery.md`、`references/work-packages/core/W0-task-framing.md` | 静态合同（含禁止采用上下文旧结论）；R02 全程零读取不回退；阶段 03/05 作者及 C/V 零旧资料访问 | 获取失败的完整覆盖有限；不删除旧任务或全局清除历史 |
| M2-6 上下文边界 | 声明从零时核对实际输入上下文、自动历史加载及工具访问；禁用边界传递到临时子任务与交付前核验 | W0、`references/recovery.md`「关闭复用的上下文边界」、`references/host-tools.md` 能力预检、W10 独立核对传递 | 既有链路证据（边界逐字传递、工具访问零命中、同任务恢复）＋Flash 机制证据（子代理完整组装输入内容级核对，父历史与项目记忆零命中），组合方式经用户批准并复评通过 | 机制证据限 ZCode 3.14.1、本次配置、GLM-5.3-Flash、普通 Agent 派发；原运行组装输入已被轮转删除、不可恢复，该历史限制保留；不外推跨模型、跨宿主普遍成立（见 §3.2） |
| M2-7 追溯／唯一采用 | manifest 记录本地位置、输入与五键 provenance；检查点记恢复和用户决定，owner 记录语义差异及采用依据；同用途最终版本唯一，多个支持原文可并存；采用变化只复查直接下游，未采用或失败版本保留 | `references/artifact-contract.md`、`references/checkpoint.md`、`references/report-guidance.md`、`scripts/check-run-artifacts.py` | 机械：execution 回归（多支持原文不判重复、唯一采用、五键、时间、相对定位）；R01 五键 provenance 实跑；B1 §3 manifest 登记与实际一致 | 语义采用由 Agent 与独立评审负责，合同已声明 |

### 3.2 M2-6 上下文隔离的组合证据与限定

按用户 2026-09-22 批准的取舍执行：原运行组装输入已不可恢复，不将恢复历史记录作为
前置；采用「既有链路证据＋未来机制证据」组合对账 M2-6：

- **既有链路证据**（ZCode 桌面端协调方式，2026-09-21）：作者请求、顶层代发与 C/V 实收
  逐字节一致（开关 false 与关闭边界逐字在内）；C/V 各恰好一次 Read 当前源、标记零命中；
  作者续接携带自身记录且仅此（同任务恢复合法输入）。
- **Flash 机制证据**（2026-09-22）：新建主会话＋两个串行普通子代理（均 GLM-5.3-Flash），
  父历史标记与自动加载记忆片段在主会话留存确认后派发 S1/S2；两子会话组装输入实际内容
  相同，父历史标记与记忆片段在其全部输入中零命中；逐会话即时 SHA-256 归档与源一致。
- **组合结论**：M2-6 各维度具备直接证据，复评通过，十二条功能闭环。原运行不可追溯的
  事实保留为历史限制；不支持结论外推为跨模型、跨宿主或所有运行普遍成立。
- 自动历史注入在其他宿主或未来版本的不可观察性保留为能力边界；宿主不能落实或证据不足
  时按规则记录能力缺口，不写成完整隔离通过。

### 3.3 既有安全与核验要求

提前交付仍完成两轮自检，披露实际深度与缺口；W10 定稿前独立证据核对在关闭复用时同样不得查找、读取或采用旧任务资料。取消覆盖当前研究及已有宿主任务，不以正式并行调度为前提。取消、调整与恢复按本轮实际预算、重试、授权和局部停止边界处理；仅超时不自动降深或省略核验。

<a id="phase5-execution"></a>

## 4. 上下文、正式并行与三项优化（原第 1、3、4 组）

交付长材料分批与转交规则、正式子任务派发和汇合、任务内共享取数、汇合不重做及交付先于锁后评分。既有独立核验和依赖规则保持；不新增调度平台、压缩引擎或缓存框架。

### 4.1 十一行完整行为、实现入口与验证

下表列出长材料、正式并行与三项优化的条件、例外、顺序和禁止项。
实现入口相对 `skills/hetu-stock-analysis/`，测试相对仓库根。测试文件简称：
capability＝`tests/product/skill/test_phase5_capability_contract.py`；
parallel＝`tests/product/skill/test_phase5_parallel_contract.py`；
prompt＝`tests/product/skill/test_prompt_contract.py`。

| 所属组／行为 | 必须保留的完整规则 | 实现入口 | 验收依据 |
|---|---|---|---|
| 1／分批与完整覆盖 | 按问题控制每批字节量，每批读后立即处理；必要全文分批读完，不丢例外、否定、条件；50,000 字节仅未校准参考，不变成获批上限 | `references/host-tools.md`「长材料分批读取与整理」第 1–2 段 | capability：`test_byte_budget_is_uncalibrated_reference_with_per_batch_continuation`、`test_batching_does_not_truncate_must_read_rules_or_skip_full_documents`；L1–L3 实际读取与回答（§4.3） |
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
约束能力预检与独立核对入口。

`tests/product/fixtures/phase5_parallel/` 包含十二份夹具；`review-expectations/README.md`
仅评审侧，答案未注入 `confluence/` 或 `violations/`（由
`test_review_expectations_live_only_on_the_review_side` 约束）。

### 4.2 R1 已关闭：实际复用开关传递到首次派发与重派

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

M1/M2 其他要求不变。回归：
`test_phase5_parallel_contract.py::test_formal_dispatch_and_redispatch_pass_actual_reuse_switch_and_boundaries`
在旧规则下检出 ValueError、修后通过，节内断言覆盖范围与顺序；
`test_phase5_reuse_contract.py::test_reuse_false_boundary_passed_to_subtasks_and_independent_review`
更新完整传递句，仍逐项保留临时子任务与交付前独立核验，未删、skip 或弱化断言。

### 4.3 历史行为证据与保留限制

下表分列合同／受控行为与真实宿主证据，不以历史计划勾选代替能力证明，也不推翻已观察行为。
路径相对本机 `.hetu/validation/phase-5/`；原件只读、不入库，工程门禁不依赖该目录。

| 证据入口 | 可沿用结论 | 保留限制 |
|---|---|---|
| `20260911-stage03-execution/l-samples/`（L1–L3） | L1 七类语义区分及十一项转交保留；L2 丢否定负例检出；L3 实际四批读取、回源与拒绝编造 | 不能证明 50KB 安全阈值、真实超长上下文或原生 compact 效果；L3 预期事实未写入素材，不能当作召回成功；容量可见路径未获同层实测 |
| `20260912-stage04-parallel/p-samples-summary.md`（P01–P06，含 2026-09-12 用户批准的解释订正） | 决策与裁决行为按合同／决策验证范围保留 | P01 未实际派发、P04 未实际重派、P06 未实际验证正常任务继续；决策样本不单独证明实际派发 |
| `20260912-stage07-host-acceptance/b2-stage04-handover/record.md`（B2） | 实际派发、局部失败重派、依赖顺序、隔离写入、串行合入与恶意材料不执行 | 部分 transcript 与用量已轮转，完整 host-support check 未通过；不能宣称全上下文注入隔离已证明 |
| `20260917-stage07-minperf-chain/record.md`（三项优化短链路，含两轮订正） | 三项优化实际发生，独立核验与质量处理保留 | 合成短链路，不证明完整股票研究提速或 token 节省；实际工具执行时间与模型消息时间分开 |
| 本文 §3.2、§8.2（M2-6 组合证据与预算方法） | 已批准的链路与 Flash 机制证据组合、预算与停止方法作为既有能力依赖 | 不扩大至跨模型、跨宿主；不恢复原运行已丢失的输入；不把方法闭环说成在线控制器；不重开 M2-6 |

原生压缩、容量可见与安全读取量未证项仍属原第 1 组：未验证组合不声明长上下文验收通过，
按参考值保守执行并披露缺口。实际 compact／恢复认证须另批最小范围与预算，不要求补齐
所有原生宿主组合；规则交付本身不产生证明，重验按 §1.1 的直接语义影响规则。

<a id="phase5-extensions"></a>

## 5. 第三方扩展管理（原第 5 组）

### 5.1 产品行为、入口与验证

实现入口相对仓库根。extensions＝`tests/product/skill/test_extensions.py`；extension_cli＝`tests/product/cli/test_extension_cli.py`；artifact＝`tests/product/skill/test_artifact_checker.py`；command_tree＝`tests/product/cli/test_command_tree.py`；entrypoints＝`tests/product/docs/test_product_entrypoints.py`。

| 组／行为 | 必须保留的完整规则（条件、例外、禁止项） | 实现入口 | 验收依据 |
|---|---|---|---|
| 5／生命周期 | 候选先校验后安装；安装后默认禁用；默认版本按数值感知排序（数值段按数值比较、前缀与混合 token 有确定次序）；显式绑定版本不因默认变化而变；按宿主独立绑定，禁用一个宿主不影响其他；update 发布新版本、既有绑定保持旧版本；任一宿主绑定仍在时拒绝 uninstall；卸载目标逃逸受管根（绝对路径、`..`、符号链接）时拒绝且不改外部文件与登记表 | `src/hetu_stock/skill/extensions.py`、`src/hetu_stock/cli.py`（`skill extension` 九叶子：list／inspect／validate／install／update／enable／disable／uninstall／context）、`src/hetu_stock/skill/__init__.py`、`src/hetu_stock/skill/work_packages.py`（`third-party` kind） | extensions（含 `test_uninstall_fails_while_another_host_still_binds`、`test_uninstall_refuses_registry_key_escaping_managed_root`、`test_install_rejects_*_without_touching_external_files`、数值感知默认版本组）；extension_cli；fixtures `tests/product/skill/extension_fixtures.py` |
| 5／失败关闭 | 硬依赖缺失、完整性破坏、兼容性不满足时候选整体关闭并给出可定位原因；扩展正文不可信（inspect 只读登记元数据，不展示正文）、不执行附带脚本、拒绝未声明文件与符号链接逃逸；管理命令不启动股票研究；扩展声明新权限必须先取得用户授权 | 同上；`skills/hetu-stock-analysis/references/host-tools.md`「扩展加载与管理」节、`checkpoint.md`、`report-guidance.md`、`work-package-result.md` | extensions 依赖解析／循环／重访环、完整性、越权正反例；command_tree 闭集（十既有叶子＋九扩展叶子，无多余）；entrypoints 文档断言 |
| 5／加载记录 | `run.extensions` 仅记录实际加载扩展的五键元数据（`id`、`version`、`source`、`summary`、`enabled_scope`，均非空字符串），不记录未启用、校验失败或本宿主禁用的候选；采用／排除决定与原因记入 checkpoint 扩展加载摘要与报告扩展附录（被关闭候选只记原因不记正文）；扩展能力不替代 W0–W10 官方工作包；未登记身份不认可 | `skills/hetu-stock-analysis/references/artifact-contract.md`（run.extensions schema）、`checkpoint.md`（扩展加载摘要指令）、`report-guidance.md`（扩展附录）、`skills/hetu-stock-analysis/scripts/check-run-artifacts.py` | artifact（run.extensions 闭 schema 六变异＋正反例） |

<a id="phase5-g5-acceptance"></a>

### 5.2 负向行为要求、隔离设计与限定接受

#### 5.2.1 要求与完成标准

第三方扩展默认不加载，用户显式启用后可见来源、版本、完整性摘要和适用范围；绑定按
宿主独立保存。依赖缺失、版本不兼容、ID 冲突、硬依赖循环、完整性失败或权限超界时
失败关闭，对核心研究仅产生明确局部缺口或停用结果。安装／更新／禁用／卸载遵守
原子替换、备份与回滚；扩展不接管研究，管理请求不启动股票分析。实际加载按五键
元数据记入 run.extensions，采用／排除决定和原因记入 checkpoint 与报告附录，不存隐藏思维链。

必需工具缺失等不可用条件下，ZCode 必须安全拒绝或停用，不伪造启用或绕过失败关闭。
原完成标准是在可控、可恢复的真实条件下直接证明该行为；刺激不能构造时如实报告
能力限制，保持未证明。本批按 §5.2.3 的知情接受收口，不改称原负例完整证明。

#### 5.2.2 设计与安全边界

先离线盘点共享 CLI 入口、安装版本和登记表并建立快照；真实探针逐卡批准，中性任务
不含预期答案。受限 PATH 曾被绝对路径绕过，不能单独构成工具不可用证据。

用户批准本机进程隔离：工具物理存在，但探针进程树不可读／不可执行（EPERM，叶子名
仍可枚举）；全新隔离 macOS、独立 OS 用户或容器不作为必备条件。临时实例可在已批
前提下使用 --no-sandbox，外层 seatbelt 保留；凭据仅复制至本机临时目录、结束后销毁，
探针登记表独立。不得隐藏、移位共享入口或修改共享权限。两卡共用实例时先 G5 后 G7
（安装后缺工具前提失效），G7 使用新对话，任一卡共享状态异常即停止另一卡。

正常结束、失败、中断或放弃都丢弃临时环境并核对共享入口可用性、安装版本和登记表
前后快照；不一致即停止后续工作并报告。不能安全构造或不能证明时如实披露，不以
CLI 测试、合成材料或口述替代真实会话行为，不新建无关框架。

#### 5.2.3 验收与批准取舍

G5-P1b 完整会话因“扩展来源未配置”停止，观察到零伪造、零启用、零成功宣称及共享
安装／登记表零接触；全程未触及 hetu-stock。用户限定接受这些结果，并将“因管理 CLI
缺失而拒绝”无直接实证列为非阻断缺口，决定不再补探针。

管理生命周期、失败关闭、歧义先澄清、下载失败不伪造、四宿主管理主流程和 F1 修复
证据继续有效；Claude E3A 后续绑定与 E1 加载归因的获批补验保留，其他宿主不追加本期复验。

### 5.3 定向修复 F1：更新中断后的同版本重试

**F1 已修关闭（2026-09-27，产品分支 `codex/phase5-f1-update-retry`）**：上项登记中的
「扩展更新中断重试」——`update_extension` 在目标目录建立后、登记前中断时，登记表、
旧版本文件与宿主绑定字节不变；同版本直接重试成功：经 `_managed_version_dir` 边界校验后
清理上次未登记的残留目录再重建，最终仅新增候选版本，不自动切换旧绑定；已登记同版本
仍拒绝（「版本已发布且只读」，本次行为验证复核）。直接测试
`tests/product/skill/test_extensions.py::test_update_retry_after_interrupted_copy_recovers_and_keeps_old_binding`
先红（旧行为：残留目录使重试在 `mkdir` 抛 `FileExistsError`）后绿；本次验证
`tests/product/skill/test_extensions.py` 与 `tests/product/cli/test_extension_cli.py`
直接回归 **74 passed、0 failed、0 skipped**。

### 5.4 后续已交付修复：按工作包判环与卸载失败恢复

固定历史提交 `4c59991`（PR #13，合并 `7e76a25`）在既有第 5 组规则内修复两个直接行为问题，已交付，既有验证结论继续有效。

- `_resolve_bindings` 按实际选中版本的工作包 ID 和 `start_requires`／`finalize_requires` 建硬依赖图，`may_reopen` 不参与；不以扩展级 requires 并集判环，合法多工作包依赖保留。新 enable 造成环时拒绝且旧绑定／登记不变；context 对已有环关闭环所属扩展并传播依赖失效，无关扩展保留。
- `uninstall_extension` 先提交登记移除、再删包。登记提交失败时旧包与登记保持原状；删除失败报告错误，残留目录保留为恢复信息，修复权限等条件后同一卸载命令可继续完成。残留恢复也不能绕过任一宿主绑定保护；不得用 `ignore_errors` 误报成功。CLI 捕获 `ExtensionError`／`OSError`，非零退出并给出原因。
- 直接行为回归位于 `test_extensions.py`：`test_enable_rejects_cross_extension_cycle_and_keeps_old_binding`、`test_context_closes_existing_cycle_propagates_and_keeps_unrelated`、`test_enable_allows_acyclic_multi_work_package_dependencies`、`test_uninstall_delete_failure_is_not_success_and_retry_completes`、`test_uninstall_registry_commit_failure_keeps_package_and_registry`、`test_uninstall_recovery_refused_while_host_still_binds`；CLI 回归在 `test_extension_cli.py`。测试名是现存验证入口，不表示本轮重新运行。

<a id="phase5-metering"></a>

## 6. 计量、宿主事实与验收工具（原第 6 组及第 7/8 组相关记录）

### 6.1 产品行为、入口与验证

host_acc＝`tests/product/validation/test_host_acceptance.py`；capture＝`tests/product/validation/test_capture_host_cli.py`；protocol＝`tests/product/validation/test_host_cli_protocol.py`；native＝`tests/product/validation/test_host_native_interfaces.py`；adapter＝`tests/product/validation/test_native_adapter_contract.py`；lock＝`tests/product/validation/test_phase2_lock_run.py`；doc_checks＝`tests/product/engineering/test_document_checks.py`；skill_pkg＝`tests/product/skill/test_skill_package.py`。entrypoints 同 §5.1。

| 组／行为 | 必须保留的完整规则（条件、例外、禁止项） | 实现入口 | 验收依据 |
|---|---|---|---|
| 6／计量归属 | 请求→message→part→callID 逐级归属，工具按 callID 关联；模型请求按 `(logical_request_id, attempt_index)` 分别计量（重试分开计量），同一 attempt 的重复合法快照按身份去重、同 attempt 两行结算值冲突时发布前拒绝；未知用量不得当作零（保持未知），无法证明结算时拒绝完整导出；同一只读快照上结算，历史账目不重算 | `scripts/host_acceptance.py` | host_acc（4566 行合成数据库正反例）；fixture `tests/product/fixtures/host_acceptance/synthetic-verifier-usage.jsonl` |
| 6／发布与清理 | db-export 全部验证通过后才发布（任一会话验证失败则不写任何文件），写失败只清理本批创建的半成品，既有产物不覆盖（重跑抛可定位拒绝）；check 验收不通过时仍把 `passed=false` 结论与失败原因保存到新输出（不能静默跳过、不把 skip 算通过），并非零退出即弃；计时不完整（含 delivered_at 缺失且无具体 gap）不得通过 | `scripts/host_acceptance.py`（db-export/check）、`scripts/phase2_lock_run.py`（交付定位输出） | host_acc 相应用例；lock 正本路径断言 |
| 6／flag 提取 | 仅从帮助文本声明区提取 flag；`accepted_flags`／`checked_absent` 策展名单保留；宿主二进制缺失时保护已有有效捕获（不改写、非零退出），首次无记录仍记录 unavailable | `scripts/capture_host_cli.py`、`skills/hetu-stock-analysis/hosts.json` | capture；protocol；四宿主夹具离线核对（`tests/product/fixtures/host_cli/`：claude/codex/opencode/zcode＋native-interfaces） |
| 6/7／离线入口 | `check.sh` 支持显式 `HETU_HOST_EVIDENCE` 证据入口，默认不触发宿主验收；验收 workflow 仅手动触发；支持事实可查；不把工具通过当组合认证 | `scripts/check.sh`、`.github/workflows/host-acceptance.yml`、`README.md`、`docs/agent-skill-usage.md` | host_acc `test_071a_check_sh_*`（默认不触发／带证据选择通过）；doc_checks（workflow 手动触发／secrets／env 注入）；entrypoints |
| 7／事实记录 | 宿主原生接口事实表与字段白名单如实登记；协议测试仅只读 `--version`／`--help` | `skills/hetu-stock-analysis/references/host-tools.md`（事实表）、`hosts.json` | native；adapter；protocol；skill_pkg（hosts.json 字段白名单与 MANIFEST 哈希覆盖） |
| 8／历史审计 | 七项 writeback 审计以 `.hetu/validation/phase-5/20260909-stage02-calibration/impact-map.md` 为输入；仅证据完全缺失时带理由 skip（skip 不代表性能验收通过）；路径存在但损坏、不可读或为同名目录时照常失败，不得伪装缺失 | `tests/product/skill/test_phase5_parallel_contract.py`（七项审计＋`_writeback_section` 等 helper） | 本机证据存在时七项全部运行通过；干净克隆缺失时按 skipif 跳过；损坏／目录负例实测失败（§9.4） |

<a id="phase5-g6-acceptance"></a>

### 6.2 三栏判定、原五运行处置与限定闭合

#### 6.2.1 要求与判定方法

正式 ZCode 验收逐运行分判三栏：①主／子 Agent 各角色 token 用量、任务与尝试身份
关联（logical_request_id／attempt_index）；②从请求到用户可打开报告的交付端点；
③隔离形式与覆盖。各栏须有直接证据；
通道恢复或工具通过不替代完整任务计量。输入／输出未知标“未提供”，不填零或从账户
额度、文本长度、调用数推测；有缓存命中输入 token 时一并记录。

缺口分因：可定位的错误统计、漏报或误判通过另报修复，获批后仅对同批既有数据重算，
不为此启动新运行；原运行未采集、轮转丢失、端点未观测或缺角色证据，不推断或伪补，
必要补证只能另批新正式运行，端点观察和隔离探针随该卡执行。任一栏未证明，不得
自行宣布完整任务计量成立；缺口文件随最终证据目录保留，不删除失败或回写旧 check。

用户批准样本集方案 A，并最终限定本组终判为 S4/S6/S7/G67-R1/G67-R2 原五运行，
不要求历史 18 次追平；恒瑞事后样本不扩大该范围。旧 C2 交付缺失、D1 用量／轮转、
CQ/S3/C1 隔离、S3 review 角色缺失、四期身份导出／协调者分栏等缺口仍保留。

#### 6.2.2 逐运行处置与限定闭合

| 运行 | 原始结果 | 用户批准的处置 |
|---|---|---|
| S4/S6/S7 | check passed=true，三栏成立 | 复用有效证据 |
| G67-R1 | 实际 quick；隔离 fail；check passed=false／not_established，5 条隔离 failure | 含诱饵标记的 runbook 被置于核验代理可读目录，污染核验上下文；失败被正确检出、定位并作为失败样本关闭，永久留第 7 组分母，不称运行通过 |
| G67-R2 | 实际 quick；iso-probe2 PASS；check passed=false（1 条 unconsumed_tail＋2 条 dwf 会话覆盖失配） | staging 内容原生 sessionId、db dwf_actor／session_task_link、watch-state 一一对应，接受既有证据作本次隔离覆盖限定补证；不称检查器已修好 |

G67-R2 的 unconsumed_tail 按用户决定忽略，原记录不改写，尾段明细和检查器修复不带入
main。research 辅助请求 acf06fcf-d4c0-49bd-8fdd-994e7d39c6eb 在原生 db 少一行，
输入 1,938／输出 409 已由 rollout／usage-events 补足并计入 check 总额
43,244,853／252,819。db 差额占本任务输入约 0.0045%、输出约 0.162%，用户仅接受
该条可精确量化缺行为非阻断，不形成未来缺口的 20% 自动豁免。db-export 未过滤现存行，
ZCode 未写入原因未定位，不推测、不追加运行，不记 db 单通道完整。

**本组限定闭合：** 原五运行用量与交付有直接证据，隔离均有直接判定或批准补证，真实
失败按失败关闭。G67-R1/R2 原始 check 仍为 passed=false，不改判成功、不回写原件。
历史第三模型 R1 与上述两次运行分列，保留其 B 类限定、7 项 failure、247 事件及
1 条 unconsumed_tail，不能据此取得性能基线资格。db-delivery FileExistsError、
HETU_HOST_TOKEN 无消费方和死代码等非阻断登记不自动升格。

### 6.3 定向修复 F2：双表 NULL started_at 拒绝完整导出

**F2 已修关闭（2026-09-27，产品分支 `codex/phase5-f1-update-retry`）**：上项登记中的
「NULL `started_at` 行静默掉出导出窗口」——`scripts/host_acceptance.py::_zcode_db_events`
在窗口查询前分别核对声明会话的 `model_usage` 与 `tool_usage`，任一行 `started_at` 为
NULL 即抛 `_ZcodeDbExportError` 拒绝完整导出，理由含会话、表名、行数与「窗口归属
不可证明」；两份导出产物（`usage-events.jsonl`、`db-export.json`）零写出。正常时间的
合法 callID/part 关联仍导出成功且工具归属正确；既有产物保持 create-only 不被覆盖
（`test_db_export_existing_outputs_are_refused_before_any_write` 回归覆盖）。直接测试
`test_db_export_null_started_at_refuses_export`、
`test_db_export_null_tool_started_at_refuses_export`（含同输入正常时间成功对照）、
`test_db_export_null_started_at_only_rows_reports_unplaceable_not_absent` 先红（旧逻辑：
混入 NULL 行时错误导出成功；仅 NULL 行时误报「no model_usage rows」缺失）后绿；本次
验证 `tests/product/validation/test_host_acceptance.py` 全文件回归
**189 passed、0 failed、0 skipped**。db-delivery 重跑 FileExistsError 等其余登记项维持未修。

### 6.4 宿主声明与历史审计的边界

宿主原生接口事实表、`hosts.json` 白名单、native interfaces 快照、离线证据入口和手动 workflow 只是事实与工具面，不独立产生宿主／模型组合正式认证。七项 writeback 审计只检查 2026-09-09 stage02 校准 impact-map 的阶段 04 追加式四列回写、真实测试名引用及待定数值不冒称收益，不是性能采样；缺文件才带理由 skip，损坏、不可读或同名目录照常失败，不推广为活功能测试跳过。

Claude `--permission-prompt-tool` 因描述续行泄漏从 `accepted_flags` 移除，支持性未获证明，不加入 `checked_absent`，不据此推断不支持。ZCode 夹具 unavailable 表示没有 CLI 二进制；app/db 观察能力与其分判。schema 未公开，无法核对实际结构时失败关闭；CI 托管 runner 无本机 ZCode app/db，改输出路径不能补齐能力，原 6-B4“重复运行固定路径必失败”的缺陷已撤回。

<a id="phase5-certification"></a>

## 7. 质量与支持认证（原第 7 组）

第 5/6 组工具交付与第 7 组质量认证分别判定；深度、多模型与来源切换专项见 §7.2。

<a id="phase5-g7-acceptance"></a>

### 7.1 组合归组、门槛、最终台账与支持声明

#### 7.1.1 要求、归组与本批门槛

正式安装须证明真实安装、自动发现、正／反／边界触发，以及引用资源按需读取、任务
开始不全量加载。研究质量须单独验收，安装成功或能力预检不能替代。

归组采用两层规则：稳定的支持组合身份包括宿主、可识别模型或能力、上下文能力／
配置、关键工具、数据模式、Skill 相关行为语义和声明深度；模型或配置有实质差异
不得合并凑数。逐运行另记实际深度、正常／故障／阻断及适用类别、验证时点、Skill
精确版本、独立性、质量、追溯、计量与隔离；这些字段差异不自动拆组或使旧结果失效。
模型身份按 model_id 登记，provider 前缀留作计费路由记录。

正常样本须达到请求深度；故障按原运行登记的触发及预期恢复／降级判定，阻断按安全
停止判定，不能仅凭降档改算故障样本。同配置不同时点／类别可归同组，不同模型或
实质不同工具／数据模式不能合并。

2026-09-30 用户批准：本批仅 ZCode×GLM-5.3-Flash×standard×public，**至少 5 次
独立端到端运行计入、至少 4 次合格、通过率至少 80%**，零未解决发布阻断缺陷、关键
主张追溯 100%。适用失败保留分母，失败修正重跑记录不得删除；故障／阻断覆盖后置，
未取得的类别不声明支持。长期 V1 §9.1 每组合 ≥10 次／≥90% 与完整样本矩阵不变。

#### 7.1.2 最终台账与单样本例外

| 样本 | 合格分子 | 计入分母 | 依据与限制 |
|---|---|---|---|
| S4/S6/S7 | 3 | 3 | 既有三栏及锁后独立质量评分成立 |
| G67-R1 | 0 | 1 | 真实失败永久留分母，失败关闭不等于合格 |
| G67-R2 | 0 | 0 | standard→quick；用户批准个案不入账，按未变更方案 A 应入分母 |
| 恒瑞医药 600276.SH（2026-09-30） | 1 | 1 | 请求＝实际 standard、public、Flash 身份可回源；锁后独立质量通过，含下述两项知情例外 |
| 合计 | **4** | **5** | **80%**；不排除 R2 时为 4/6，例外差异明确保留 |

恒瑞仅本样本获批两项例外：①未预立启动前隔离／同期 watcher，接受原生记录事后取证
和锁后评分，隔离仍未验证，不称原始 check 通过；②读取旧 R2 request.md 与 manifest.json
文件名／状态清单，违反 reuse_previous_task_data=false；用户知情接受计数，不称完全
从零。未观察到读取旧报告、旧原始材料正文或采用旧财务数值，例外不推广到后续运行。

研究树于 2026-09-30 12:26:17+08:00 事后锁定，报告 SHA-256 为 6b373227…619d0；
机械检查 14/14、manifest 哈希 77/77 一致，Codex 锁后独立评分六维质量通过，关键
主张补充追溯表保留原检查器 105 条 warning，不称 warning 清零。用量
45,268,207／209,768 不含 session_title；db 少 4 条辅助请求由 rollout 补足，不称 db 完整。

#### 7.1.3 安装链、历史结果与支持声明

G7-I1 以临时 HOME／XDG 用现行安装器真实安装；不适用请求零触发零加载，适用请求
自动发现 Skill，触发后才首次读取按需资源，未在启动时全量加载，共享状态快照不变。
安装／发现／触发／按需读缺口闭合，C2/C3/C6 既有触发边界证据复用；该合成会话不计
研究认证次数，后续超出观测目标的执行和超耗见 §9.5。

历史 standard×public 10/10 质量、quick 6／deep 1 质量记录、688048.SH 深度回归及
五槽／三域 D5／B 类限定验收继续有效，不机械并入本批计数，也不替代完整组合认证。

**本批支持范围**仅上述目标组合的正常 public 研究，获证类别为 S4 科创板半导体制造、
S6 主板钢铁、S7 主板消费白酒、恒瑞主板医药制造。上下文限原生验收会话配置，未记录
数值容量、不作容量承诺；关键工具限留痕的 Skill、文件与公开网页取证能力及
hetu-stock-analysis 相关行为语义。quick/deep、其他宿主／模型／数据模式、未获证类别
维持 UNVERIFIED，其他宿主功能层结果不等于完整研究支持。

支持声明留存宿主、模型、上下文、关键工具、数据模式、Skill、验证日期，并保存脱敏
输入、必要工具轨迹、结果、评分、时间、token 汇总与计数依据，不存隐藏思维链、
无关遥测、凭据或个人绝对路径。用户据此可查明哪些研究在什么条件下获本批限定认证。

<a id="phase5-depth-model-source"></a>

### 7.2 深度、多模型与来源切换专项的最终验收

专项要求 quick、deep 各至少一份真实报告，同一中性 standard 目标在三个不同、可核验模型上各一份；三个获批缺口域分别证明首选受控禁用与真实替代切换，完成批次终审并由用户最终批准。既有有效结果按实际深度与实质条件映射，不因请求文字、日期或版本不同自动排除或重跑。

2026-09-27 独立技术评审通过，用户按 D5／B 类限定口径最终接受五槽与三域，长期 §3.7 本专项完成：

| 槽位 | 已映射运行 | 判定与边界 |
|---|---|---|
| `quick` | S2（实际深度 `quick`） | 探针 v2 通过，锁后独立评分六维通过。原始 G2 条款按实际深度定档，不要求请求深度也为 `quick`。 |
| `deep` | CD（请求与实际均为 `deep`） | 隔离证据、锁后评分及回填检查成立。 |
| `standard` × GLM-5.3-Flash | S6 | 同一中性目标的 `standard` 报告，隔离证据及回填检查成立。 |
| `standard` × GLM-5.3 | A1 | 质量、深度、交付与目标映射成立。用户 D5 只接受工具读取与输入来源的 db 扫描口径；请求载荷的初始加载／历史注入及检测器反例控制未证明，两种隔离证据形式未证明等价。 |
| `standard` × GLM-5.2 | R1 第二次启动 | 质量综合 9.0/10、锁后 56 项抽验 54 项吻合、实际深度 `standard`、交付成立。用户 B 类知情接受仅使本槽位计入：受限方行为面零越权，`initial_load`／`history_injection` 资格维度不可证；不改变后续运行的探针标准。 |

R1 原始 check 保持 passed=false、baseline_qualification=not_established、7 项失败和 1 条 unconsumed_tail。最终 247 条事件的已记录用量无 usage gap，不能称零收集缺口或完整性能基线；早期 236 条统计已订正。D5／B 类接受不改后续探针标准、不授予性能资格。

| 缺口域 | 受控禁用与替代证据 | 判定边界 |
|---|---|---|
| 官方交易状态 | 结构化适配器产生 `called=false, disabled=true, status=null` 禁用信封；腾讯／新浪同日行情快照与公告面交叉核验。 | 首选禁用是预设短路，不计作请求失败。 |
| 独立行业份额／竞争证据 | 该域只有来源合同，没有已接入适配器；运行前的禁用声明与替代来源原文、采用记录在案。 | 替代材料仅支持方向性竞争判断，**不证明精确行业份额或独立原始统计已取得**。 |
| 公告分页与同日发布时间 | 结构化适配器禁用信封在案；上交所公告查询与巨潮窗口、官方静态公告材料交叉核验。 | “未发现”按可核验范围表述，不扩大为“没有公告”。 |

锁后评分与独立技术评审核对禁用、替代材料和 L0 核验链，并补齐二期 G2 批次终审。批准不授权新运行或预算，不替代 quick／deep 每组合 10 次的完整认证、性能验收或 V1 整体完成；其他宿主按 §1.1 备案。[验收证据 §2–§4](validation/2026-10-03-phase-5-acceptance-evidence.md)保留原件定位、批准边界与统计残差。

<a id="phase5-budget"></a>

## 8. 预算、计量方法与未验收性能（原第 8 组）

### 8.1 三档策略默认、重试与日常停止

三档策略默认（v1，2026-10-01 用户批准）已进入 `references/orchestration.md` 与 W0 实际采用记录；默认批准不等于充分实测校准或性能达标；[数据依据与校准限制](validation/2026-10-03-phase-5-budget-evidence.md#4-默认参数依据与校准限制)单独保留。

| 深度 | 外部调用上限 | 同类重试上限 | 并行度上限 | 授权数据费用边界 |
|---|---|---|---|---|
| quick | 120 | 3 | 3 | 不新增付费授权 |
| standard | 120 | 3 | 3 | 不新增付费授权 |
| deep | 260 | 3 | 3 | 不新增付费授权 |

外部调用指外部检索或取数，失败调用也计入，本地读写与计算不计入；外部调用与总工具调用分别标记。内部重试／批量行为不能精确观测时记估计值、依据与缺口，不宣称宿主精确计数或实时硬截断。并行度为上限，不要求派满；派发仍由独立性、依赖和宿主能力决定。

准备与收尾预留按本轮上下文和预计准备、核验、修正、交付请求估算，输入／输出分列并计入总预算，预留转实际后不重复累加。纯验收探针和锁后评分按卡另列，不成为日常研究固定消耗；未知不当零。授权费用未知按许可与供应商可执行限额记录，不当无限额度；已有订阅不等于新增费用授权，新增付费须明确授权和额度。主 Agent 可按问题调整并说明预期收益，W0 记录实际值及理由。

同对象、来源和失败原因累计同类重试；到限换合法路径、缩小或留缺口，不继续同类失败请求。七类停止分别作用于对应工作：深度满足、检索价值过低、非时间预算耗尽、取消、能力不足、授权不足、安全阻断；不合并成通用停止，不把其他工作标为完成。authorized 授权不足只停止相关数据，不静默改 public。

仅超出目标时间时继续必要研究与核验，不请求续时、不暂停、不降深或省核验。进度与 checkpoint 至少记录目标值、实际耗时、卡点、完成域、剩余工作、关键缺口、预算和下一动作；交付保留实际总耗时与说明，无目标或不可观测不造数值。用户取消及其他预算／安全停止仍生效。

<a id="phase5-budget-method"></a>

### 8.2 B1–B6 历史对账、预算合同与离线验证

本节为逐批批准的成本合同及离线方法；其中墙钟截止指具体已批准批次的截止，不把日常研究目标时间改为自动停止阈值。日常超时行为仍按 §8.1。

#### 8.2.1 三个历史批次最终账目（B1–B4，2026-09-22 对账并复评通过）

逐请求底账 128 行（原生 `computed_total_tokens`，按请求 ID 去重、重试独立计量、缓存不
重复累加、缺失不按零），三视图（角色／环节／窗口）与底账闭合，37 项交叉核验通过，
两次运行字节一致，无不可恢复缺口：

| 批次 | 窗口口径 | 请求数 | 总 token |
|---|---|---|---|
| 阶段 04/05（含批前准备） | 23:14:50–23:36:58（准备 32 请求 4,451,551 单列桥接＋链路 22 请求 2,766,941＋批后 27 请求 5,390,501） | 81 | **12,608,993** |
| 原因分析批 | 提交前 26 请求 6,299,316（快照，复算成立）＋提交与报告 5 请求 1,304,288 | 31 | **7,603,604** |
| Flash 机制补证批 | 现场 8 请求 558,744＋子代理 2 请求 69,758＋宿主附加 915＋关闭段 5 请求 502,079 | 16 | **1,131,496** |

旧算术、准备漏计、窗口右端及 10 个请求环节分类已订正，明细见本节对账报告。
旧右端 23:36:36 是最终报告请求开始，实际完成为 23:36:58.169；跨端点请求按开始时间
归属并披露，不表示全部 token 在同侧产生。分类订正不改变请求总量、成员、角色或原生 token。

越线请求于 23:25:09 开始、23:25:25 完成；按完成顺序累计首次越线亦为 23:25:25，
用量落盘时刻不可证。首次 token 字段读取与首次预算求和的实际工具区间分别为
23:30:04.475–04.910、23:32:14.149–14.767（+08:00）；后者返回协调者 21 请求、
3,642,107 token 的中途快照。链路窗口内无用量查询、停止规则未执行，原生记录支持这些事实。

排除项（非本三批）：M2-6-once 段 49 请求 3,824,723、docs-check 批 107 请求 12,833,493、
harness-craft 两会话及后续评审批次（其 Harness 用量不写入 ZCode 库，未追加核算）。

复算入口：`.hetu/validation/phase-5/20260922-middle-usage-reconciliation/`（`scope.json`、
`reconcile.py`、`ledger.jsonl`、`summary.json`、`report.md`，Git 忽略，不入库）。

#### 8.2.2 预算与停止规则（B5，2026-09-22 方法定稿）

供下一批任务直接采用的方法约定（**非已部署的在线控制器**）。实测参考（历史证据，非
下一请求必然上限，不跨模型／上下文直接套用）：阶段 04/05 协调者 73 请求单请求 total
中位 165,354、原因分析批协调者 31 请求中位 249,920、Flash 批协调者 13 请求中位
79,990、子代理单请求约 3.5 万（含缓存读）。估算方法：各角色／环节预计请求数 × 单请求
消耗区间求和，**覆盖协调者、参与者、子代理、宿主附加全部角色，以及准备／执行／收尾
全过程**（准备、协调者工具续轮、参与者、已知宿主附加调用、归档和最终报告均须纳入，
收尾单列但不重复相加）；输出长度、上下文增长、失败重试与宿主附加请求的估算来源必须
写明，未知不填零。input 含缓存则不再累加缓存，缓存单列、不预设命中率、
不折算金额。单批启动前逐项填写合同（任务范围与起止事件、角色与环节、计量
字段与数据入口、预计请求数与区间、总预算 B、收尾预留 R、请求次数限制、墙钟截止、检查点、
监测缺失处置）并由用户批准，不留“以后再定义”的必需项；只填参与者执行成本不符合本合同。具体批次参数不在本文指定。

#### 8.2.3 检查点与停止决策（B6 规则摘要）

- 变量：K（已查询、可回源累计实际用量）、P（已发出未结算请求预留，不得因未结算记零）、
  Q（下一动作全部新增请求估算，并行按总和预留）、R（必要收尾预留，消耗后转 K 或 P，
  不重复占用）、B（用户批准上限，不自动上调）。
- 放行：数据完整、未进入停止状态、次数／墙钟允许且 `K + P + Q + R <= B`；放行先把 Q
  预留进 P；结算后用实际值替换预留；重复快照不累加，实际重试独立计量；迟到记录归原
  批次重算余额；实际超预留记估算偏差，不丢弃超出部分。
- 检查点：启动前；新增派发、续接或重试前；结果返回后且下一业务动作前；收尾开始时及
  最终报告前。查询并入已有工具操作并记录查询的实际工具执行区间及返回快照；等待靠
  原生通知，不为 sleep/date 巡检启动模型回合；通知不可用时按已批有界等待办法执行，
  等待次数与成本仍计入合同。
- 失效处置：读取失败、字段缺失、身份冲突或更新完整性不可判时，不把 K 当完整零值，
  默认暂停新增业务、仅允许有界必要收口；改用次数／墙钟控制须用户事先明确接受
  “token 仅估算、不可实时保证”，不得运行中静默降级。
- 停止状态：超余额、次数耗尽或到点（`now >= deadline`）即进入“仅收尾”且不再自动重开；
  禁止新增研究、补验、重试、委派；允许保存证据、安全停止、一次最小状态报告；收尾请求
  须同时满足 `Q <= R` 与 `K + P + Q <= B`；预算已超或预留不足时，优先无新增模型调用的
  本地保存／停止，当前可发出的简短报告如实说明未完成项，不继续请求模型。
- 可执行边界：Agent 只能在获得控制权时执行检查，无法截住 Harness 已自动开始的模型回合，
  该部分估入 Q/P；严格硬上限依赖宿主能力，缺口在批次启动前披露。只读用量读取按合同
  会话／请求 ID 集合查询原生 `model_usage`（ZCode 入口身份字段为 `logical_request_id`＋
  `attempt_index`），记录查询的实际工具执行区间及返回快照；其他 Harness 使用其现有
  可回源入口。最终报告自身未结算时给出截止快照与待结算项，不追加请求递归统计自身
  报告成本。

#### 8.2.4 离线验证结论与能力边界（B5–B6 关闭，2026-09-23 复评通过）

规则按 §8.2.3 实现为最小回放状态机，固定输入为 128 行冻结底账副本与已归档 part 证据副本：
V1–V10 全部通过（边界放行、P 预留防重复、迟到结算进仅收尾、身份去重与冲突异常、未知
用量暂停、收尾预留转移、停止不自动重开、次数／墙钟门限、拒绝狭窄口径、已知时间与假设
分离）；两个负向检查通过（跳过 P 预留、未知当零两个错误变体分别被 V2／V5 检出，临时
驱动已删除）；同一冻结输入两次生成 `results.json` 字节一致。首轮复评不通过的四组问题
（停止与失效状态、V1 的 1000/1001 边界、V10 行为检查、用量查询示例字段）已修复并
复评通过。

能力边界（保留）：未构建常驻监控或请求内硬中断；不承诺请求内精确 token 硬中断；不宣称
支持所有 Harness 实时监控；用量落盘时刻未知由 P 与未知处置规则应对；具体批次参数仍须
逐批启动前由用户批准。验证入口：
`.hetu/validation/phase-5/20260922-middle-budget-offline/`（`budget_reference.py`、
`validate_budget.py`、`cases.json`、`results.json`、`report.md`，Git 忽略）。

### 8.3 三条性能线的保留要求（已停止继续验收）

保留从零全流程、单股内部并行、允许复用收益三条验收线，分别记录；不能互相替代。

1. 先确认计量与隔离可证明：请求至用户收到可打开最终报告，包括准备、研究、修正、核验与
   交付；涵盖协调者、参与者、子代理、宿主附加及重试，重叠耗时和重复快照不累加。
   输入／输出分别计量，缓存字段说明口径，未知不填零；用量完整不代表隔离或交付端点完整。
2. 优先映射历史有效基线。缺少的测量须单列授权；宿主、模型、日期或哈希变化不自行使旧证据
   失效。是否可比较取决于具体输入、深度、资料、工具与独立性条件，而非元数据相同。
3. 性能改动前批准总耗时目标、工具调用约束、输入／输出 token 上限、两者一升一降的判定、
   quick/standard/deep 非时间预算及预留。当前不能编造数值；执行计划可以先安排获批基线，
   在数值批准门停止，不能带着空合同启动优化后验收或看结果倒定标准。
4. 并行专项保留至少三个独立域真实重叠、端到端中位提速至少 20%、质量不降；工具调用同时
   遵守原 125% 和五期“不超过基线”的更严格约束，token 不套用金额比例。样本、重复测量、
   配对与质量评判方式在运行前固定；单股并行不以多股票并发替代。
5. 从零主验收明确 reuse_previous_task_data=false，并证明资料访问与上下文边界；复用收益
   单列实际采用来源与省去工作。本任务合法共享输入仍可用；不开启旧资料回退绕过隔离失败。
6. 只优化有轨迹证据的瓶颈，保留深度、安全、许可、独立核验和必要研究。若采样暴露需改实现，
   先定位并形成针对性修复，不在运行中自由增加性能改造。

普通任务超出时间目标只记录并继续，用户取消、非时间预算、安全和能力停止规则仍生效。
本设计不引入请求内 token 硬中断或在线控制器。

### 8.4 真实批次成本、停止与增量验证边界（无新增运行授权）

执行计划中的真实批次合同须覆盖 §1.2、§8.2 的全部字段，并明确证券／问题、估算来源、
观察方式与退出产物；成本涵盖全部角色。预计两家以上公司时，首次分析前按 AGENTS.md
说明次数与成本、确认模型选择，不自行切换。

能力预检、隔离或计量证据不足、记录不可归属、预算或预留不足时，保存结果与缺口并按批准
停止规则退出，不追加研究。未知和未结算项按 §8.2.3 报告；新样本、续用旧批次或换宿主
试跑均需明确新授权。

| 变化／验证 | 对既有结果的处理 | 最小验证 |
|---|---|---|
| 文档状态或引用订正 | 不影响原验收 | 语义对照、文档链接、diff 检查 |
| 具体实现修复 | 仅可定位受影响行为待重验；其余有效 | 旧实现失败、新实现通过及直接回归 |
| 新增组合或缺口补验 | 旧通过结果不作废 | 仅新增／未满足场景，真实运行须授权 |
| 性能优化 | 旧结果仍对原条件有效 | 固定可比条件的直接对照，不自动整批重跑 |
| 版本、日期、分支或哈希变化 | 仅记录来源，不触发失效 | 无语义影响则不重跑 |

最终工程门禁按实际变更执行一次适当检查；无实现变化不重跑整套测试。工程通过、历史审计通过
或条件 skip 均不等于质量认证或性能通过。所有既有通过、失败和订正记录保留。

<a id="phase5-closeout"></a>

### 8.5 现行未完成项与已用额度

既有 P1／09-17 对已证真实并行重叠，但从零全流程、单股并行、允许复用收益三条数值验收线均未完成；全流程取样要求保留 standard 至少三对、quick／deep 各一对，三档默认预算未充分实测校准。A1 优化前单侧候选资格按既有锁定解释保留，不重开审批；A2 已离线核查必需信息与受限获取路径边界，是否修订仍待需求决定，不预设改判。A3／AQ／AD 未启动，P2 在 A2 资格门后停止，不续用旧授权。

X1（串行观察）与 X4（并行观察）已获批执行完毕、卡级启动额度均用尽；X4 首小时并行门 PASS 是运行时补充实证，独立核验和锁后评分未执行，尚不能形成合格性能配对或完整质量验收。X2／X3 未批准。P4-2 仅为历史诊断事项，不作线 1 或收尾前置门。成本和截止口径见[预算证据 §3](validation/2026-10-03-phase-5-budget-evidence.md#observed-costs)与[运行证据 §6.2](validation/2026-10-03-phase-5-acceptance-evidence.md)，不把未知费用填零，不新增补核验。

按用户 2026-10-02 决定，本节要求与缺证据事实保留，采样、配对、校准和 X4 补核验均不列当前待办。不请求重新批准延期。五期整体未完成，不阻断独立已交付功能继续使用。

<a id="phase5-history"></a>

## 9. 历史验证与证据入口

下列测试数、CLI 数和验证时点描述对应历史批次，不是本轮复跑结果或固定验收指标；既有结果按 §1.1 继续有效，不形成新增运行授权。

### 9.1 安装与旧成果验收及已关闭修复

#### 9.1.1 既有验收依据

| 对象 | 2026-09-12 历史验证结果 | 原始记录（相对 `.hetu/validation/phase-5/`） |
|---|---|---|
| F1／阶段 05，macOS/APFS | `tests/product/skill/test_installer.py` 33/33；真实 RENAME_SWAP、并发串行化、中断恢复、跨文件系统／不支持交换负例及临时 HOME 安装→更新→diagnose→卸载；八轮缺陷关闭，最终定向 111/111、全量 1374 通过 | `20260912-stage05-installation/record.md` §1、§4–7 |
| F2／阶段 06 | `tests/helpers/test_archive.py` 当时 8/8；脱敏绕过、越界读取、JSON 数组秘密过滤、相对路径导出四类缺陷关闭 | `20260912-stage06-extensions-archive/record.md` §1.4、§4.3–4.4、§5.2–5.4 |

F1 八轮修复覆盖组合恢复、受管归属、共享保护、旧布局、回滚三态、删除依据、switching
记录与发布后验证失败恢复。后续修复见 §9.1.3；有效性与重跑条件统一按 §1.1。

#### 9.1.2 交付验证

定向测试八项 177/177 通过；`skills/hetu-stock-analysis/` 与 main 基点无差异；
`skill validate` 通过；命令树机械断言恰好 10 叶子；`git diff --check` 与文档链接检查通过。

#### 9.1.3 全面评审修复（2026-09-20，评审基准 `789c4f7`）

7 项代码缺陷均先红后绿关闭；R3/R5 的复评剩余问题
在第二批修复后通过。行为结果如下：

| 编号 | 问题 | 修复结果 |
|---|---|---|
| R1 | 回滚覆盖非受管启动器 | 交换前核对启动器归属，非受管拒绝且不改动现状 |
| R2 | 卸载遗漏持久宿主引用 | 共享核对纳入 `hosts/*.json` 登记的实际安装路径，触碰文件前失败 |
| R3 | 回滚后段失败不一致；双故障时事务提前 finalize、重试选错目标 | 单故障恢复交换前一致组合记 rolled_back；恢复尝试前记 `restoring`，恢复器按摘要完成正确恢复，重试完成原本要求的回滚 |
| R4 | 文件后缀绕过 JSON 秘密过滤 | 按解析后的实际 JSON 内容识别，后缀／别名／目录位置不能绕过 |
| R5 | 秘密经索引及“读取范围”诊断再次输出 | 索引与诊断说明只使用脱敏显示数据 |
| R6 | 全部记录受限时无法生成索引 | 零文件可复制仍生成可读索引；已存在目标仍拒绝 |
| R7 | 未知字段结构导致导出崩溃 | 类型防护，无法映射如实说明，原记录可取回 |

历史验证：第一批直接 119／全量 1207 passed；第二批直接 68、复评直接 90／全量
1209 passed，ruff、mypy、链接、MANIFEST 与 Skill 校验通过。

### 9.2 控制与复用的工程验证

- 定向回归由 control（13）、reuse（10）、execution（19）及既有 artifact／phase2／prompt／
  skill_package／tool_cli／command_tree 组成：初次验证 465 passed，R1/R2 修复新增六项合同后
  471 passed；MANIFEST 无漂移，Skill validate 通过。
- M2-6 链路样本墙钟 267.8 秒达标，Flash S1/S2 各一次请求、组装输入逐字节归档；组合依据和
  不可恢复历史限制见 §3.2。三项文档订正未调用模型；对账与离线规则结果见 §8.2.1、§8.2.4。
- 阶段 06 最终 `env -u HETU_HOST_EVIDENCE bash scripts/check.sh`、工作区／暂存区 diff
  及文档链接检查通过。

该轮工程验证未启动股票分析、真实模型会话、真实安装更新、扩展绑定或性能对照，未重跑已通过批次。

### 9.3 上下文／正式并行的评审与验证

| 验证范围 | 历史结果 |
|---|---|
| 工程自检 | 七个实际变更 Skill 文件的 MANIFEST 重算、ruff、链接、Skill validate 与 diff 通过；无私有证据的 git archive 快照八文件回归 387 passed，未设 HETU_HOST_EVIDENCE；用户已确认 |
| R1 开关传递修复 | 首轮规范轴 0 阻断，需求轴 P2 缺口经 `2566c5e` 修复；parallel／capability／reuse／prompt 四文件 65 passed，旧规则检出缺口、现行通过，MANIFEST 一致；复评两轴问题均为 0，其他关键行为无阻断。规则见 §4.2 |

### 9.4 扩展计量评审与无私有证据回归

Skill validate、MANIFEST 与 diff 工程校验通过。各类回归按证据条件分列：

| 验证范围 | 历史结果 | skip 与失败边界 |
|---|---|---|
| 本机定向套件（105.9s） | 709 passed／7 skipped／0 failed；七项 writeback 全部实际通过 | 7 skip 均为带理由宿主条件：zcode unavailable／未安装、捕获宿主不适用、opencode/zcode 本机漂移 |
| 无私有证据的 git archive 源码副本（96.23s） | 703 passed／13 skipped／exit 0；确认 hetu_stock 实际加载副本 | 13＝7 历史审计缺证据＋6 宿主条件；未改夹具或验收边界，副本无 .hetu，独立索引仅供合同中的 git diff |
| 损坏文本／同名目录两态负例 | 七项审计各 7 failed／29 deselected／exit 1 | 有证据却损坏必须失败；未触碰原 .hetu，不用 skip 隐藏损坏 |

2026-09-25 Spec／Standards 评审均通过，无实现阻断；卸载边界、计量归属与结算去重、
只读快照、导出清理、计时失败关闭和捕获保护均获核对。原十个 CLI 叶子保留，新增九个扩展叶子。
集中修复后的历史工程结果为 1592 passed／6 带理由 skipped。

F1/F2 直接验证分别见 §5.3、§6.3；另一次历史离线 check.sh 因 claude 版本／夹具漂移有
1 failed，不称该次全量通过，也不为文档整理重捕夹具或重跑验收。

<a id="phase5-groups-evidence"></a>

### 9.5 第 5–7 组证据入口与成本偏离

原始材料仅在本地 .hetu/validation/phase-5/，不入库、对外须脱敏。以下路径相对该目录：

| 对象 | 证据入口 |
|---|---|
| 三组最终处置及例外 | 20260927-groups-5-7-repair/stage12-verdict/stage12-review-package.md（v6） |
| 离线三栏／归组盘点 | 同批 stage02-group6/、stage03-group7/two-layer-registration-raw.txt、stage08-joint/joint-qualification-raw.txt |
| G5-P1b | 同批 stage09-group5/probe2-20260929/（会话轨迹、共享状态前后快照） |
| G7-I1 | 同批 stage10-g7/g7i1-20260929/ |
| G67-R1 | 同批 stage11-joint-runs/r1-600036-standard/（隔离证据、原始／最终 check、用量、锁定报告） |
| G67-R2 | 20260928-r2-flash-600276-a1/（staging、obs、iso-probe2） |
| 恒瑞最终样本 | 20260930-g7-hengrui-retrospective/（exception.md、independence-audit.md、scoring.md、trace-matrix.md、usage-summary.json、锁定记录） |

成本按卡登记，不将历史第三模型 R1 的两启动 91.5M／431K 并入本批；未知不填零。

| 卡 | 实际输入／输出 | 审批预算（输入／输出）与偏离 |
|---|---|---|
| G67-R1 | 38.16M／0.258M | 70M／0.35M，预算内，获批单启已消耗 |
| G67-R2 | 43.24M／252.8K；另评分员 6.00M／35.6K | 70M／0.35M，预算内，原件分别留存 |
| G5-P1b | 407K／6.1K，2 会话 | 0.3M／0.03M、1 启；重启竞态双发，输入超约 36%，第二会话被工具帽打断 |
| G7-I1 | 4.37M／62.7K，1 会话 | 0.3M／0.03M；首个按需读即停止未执行，跑满 900s 时长帽，输入约 14.6 倍、输出约 2.1 倍 |
| 卡外误发 | 约 346K／6.0K | 日常实例误发 2 次，另列偏离账 |

### 9.6 原始证据入口

`.hetu/validation/phase-5/` 为本地原始证据，Git 忽略，不入库、不作为正常产品依赖；对外需脱敏。版本化摘要统一放在 `specs/validation/`：[验收证据与批准记录](validation/2026-10-03-phase-5-acceptance-evidence.md)保存运行资格、例外、失败和统计订正；[预算消耗与校准依据](validation/2026-10-03-phase-5-budget-evidence.md)保存逐运行账目、预算取舍与观测限制。现行要求和结论以本文为准，两篇记录不构成新增运行授权。

中期原始证据定位（均相对 `.hetu/validation/phase-5/`）：

| 目录 | 证明内容 |
|---|---|
| `20260911-stage03-execution/` | C01–C10／R01–R03 合成判定 |
| `20260912-stage07-host-acceptance/b1-stage03-handover/` | TaskStop、迟到未采用、候选恢复、R03 四故障 |
| `20260916-stage07-reuse-offline/` | R04 许可副本、中断重试、主体不符 |
| `20260921-middle-m26-once/observer/` | 阶段 03 派发与阶段 04／05 订正链路 |
| `20260922-middle-m26-docs-check/observer/` | 原生记忆机制与轮转官方资料核对 |
| `20260922-middle-m26-flash-mechanism-once/` | Flash 组装输入逐会话归档 |
| `20260922-middle-usage-reconciliation/` | 128 行底账、三批对账与脚本 |
| `20260922-middle-budget-offline/` | V1–V10 离线规则验证及负向控制 |

<a id="phase5-limitations"></a>

## 10. 已修问题、非阻断登记与能力限制

### 10.1 集中修复规则的保留与闭合

缺陷编号用于验收对账，不新增需求组。下表列出已关闭问题的行为与必要正反例；源码／测试是验证入口，历史结果见 §9。

| 编号 | 最小修复行为及必须保留的验证 | 现行去向 |
|---|---|---|
| 5-A1 | 默认 enable、inspect 与 update previous_version 共用数值感知排序；连续数字按数值，非数字按字符与类型标签确定排序，原串作平局键；不承诺 SemVer 预发布，不收窄安全版本字符串；显式版本精确匹配，旧绑定不变 | §5.1；跨 0.9→0.10、release-9→release-10、混合串正反例 |
| 5-A2 | 消费／写回前校验 extensions、bindings、宿主集合、记录、versions 与实际读取字段；binding 为合法 version 对象；空版本受控拒绝，保留合法缺省宿主初始化，不引入新 schema 或自动修坏表；ExtensionError 定位且不泄露正文／秘密，无 Traceback、登记字节不变 | §5.1；裸串、null、非对象、空／畸形版本负例 |
| 5-B1 | validate 测试把 XDG_DATA_HOME 隔离到 tmp_path，不依赖本机全局登记；假 provide 登记正反两态确定 | `test_extensions.py`，保留夹具隔离 |
| 6-A1 | 用实际请求→message／part→callID 证明唯一归属，不凭邻近时间、顺序、数量或 turn_id 猜；模型／工具窗口分别作用，跨窗仅只读核身份、不计窗外；缺失／冲突／孤立受控拒绝，正常链路仍导出 | §6.1；少报、数量相等但错挂、重复身份、缺归属、同 turn 多请求、跨窗正反例 |
| 6-A2 | 执行状态不等于用量结算；可靠可回源的失败消耗计入，未结算／初始化零／缺列／未知保留缺口并拒绝完整导出；非零也不自动证明结算，provenance 记录状态分布 | §6.1；正常、可靠失败／零、取消未知、混合未知与缺列负例 |
| 6-A3 | 必需 attempt_index 为非负整数，不默认 0；logical_request_id＋attempt 无歧义编码，重试分别计量、同 attempt 合法快照去重，冲突发布前拒绝；旧事件不与新导出混用，旧产物不重写 | §6.1；不同 attempt 相同用量仍分别计数 |
| 6-A4 | 全会话先收集校验后发布；既有 usage-events 或 db-export 在写前清洁拒绝，不追加旧半成品；写失败仅清理本批新文件，不动旧证据；不承诺断电跨文件原子性或引入通用事务框架 | §6.1；多会话失败零输出、已有字节不变、重跑及写失败 |
| 6-B1 | 只解析声明区短长别名，不取描述／示例／参数值；首次可用解析结果，刷新保留 accepted_flags／checked_absent 策展子集；不要求白名单穷尽 help，不把已出现选项塞 checked_absent | §6.1 与四宿主只读夹具 |
| 6-B2／6-B3 | A 组各带负例；query_source 为可选列，有则 provenance 记会话分布，无则不可得、不填零；不改总量、不冒称完整逐角色账目 | §6.1 |
| 8-B1 | 仅七个历史 writeback 在证据完全缺失时带理由 skip；有证据照常断言，损坏／不可读／同名目录不得吞异常 skip；宿主环境 skip 分列、不硬编码总数 | §6.1、§9.4 |
| 后续四项阻断 | 卸载受管边界、check 缺时点失败关闭、缺宿主保护有效捕获、续行 flag 拒绝，均先红后绿关闭；原 probe／正式计时同路径语义保留，缺失不补造、不以零替代 | §10.2 |

第 5 组的扩展 update 重试、F2 NULL 时间及跨扩展判环／卸载失败修复已分别关闭（§5.3–§5.4、§6.3）。历史统计关联错挂本身不改变原生 token，但结算与重试身份影响未来导出完整性；不能笼统保证所有路径总量不变。中期 B1–B4 由独立核算支持，不用新导出改写旧账。

### 10.2 已关闭的四项阻断问题

2026-09-25 评审确认的四项问题均属第 5、6 组组内缺陷，经用户批准修复，
包含两项取舍：缺宿主时保留有效捕获并非零退出，claude 夹具显式移除
`--permission-prompt-tool`。实现提交 `8e0177f`，先红后绿，定向回归 282 passed／7 skipped，
后续复核通过。**四项均已关闭**：

| # | 已证场景（修复前） | 修复行为 | 验证 |
|---|---|---|---|
| 1 | 登记表键被篡改为外部路径后 uninstall 递归删除外部目录 | 删除与写回前校验 ID 形态与受管边界，非法抛 ExtensionError，外部文件与登记表字节不变 | extensions 越界负例＋正常卸载／绑定仍在拒绝回归 |
| 2 | `delivered_at=None` 且无具体 gap 时 check 仍 passed | complete=False 且无具体 gap 时追加可定位失败，指明缺失时点；不补造时间、不以零代未知 | host_acc 缺交付时点负例（probe 与非 probe 同语义） |
| 3 | 宿主二进制缺失时刷新把已捕获夹具改写为 unavailable 且 exit 0 | 已有有效捕获（status=captured 且含 help_text）时不写回、明示未更新并非零退出；首次无记录仍记录 unavailable | capture tmp 夹具＋mock 正反例 |
| 4 | 描述续行文本被当选项声明提取（`--permission-prompt-tool` 即由此泄漏） | 声明段占位符抹除后含非 flag 文本即判续行跳过 | capture 合成续行负例；三宿主离线对照：codex 31 项、opencode 25 项零变化，claude 76 项仅少 `--permission-prompt-tool` |

上述四项修复中遗留未确定事项仅一项：`--permission-prompt-tool` 是否受宿主支持（§6.4）。

### 10.3 未修登记与能力边界（不自动升级为待办）

- 扩展 5-B2 两测试名称与实际格式拒绝分支不一致，只登记；5-B3 安装后增加未声明文件不被 stored 完整性摘要发现是声明文件范围的既有取舍，不扩大安全声明、不改变校验范围。validate 畸形登记表错误归因、list 字典序展示等非阻断项不夹带修复。
- db-delivery 重跑未捕获 FileExistsError 仍未修；三个工具归属拒绝分支无专门回归，但合成库行为已实证。6-C computed_total_tokens 额外必需列与一致性加固不纳入；6-B4 CI 重入缺陷撤回，托管 CI 缺本机 app/db 的能力限制保留。`HETU_HOST_TOKEN` 无消费方，不能据存在性门禁宣称认证。
- Claude E3A 采用／排除记录与 E1 加载归因已在获批 R2 场景补证；不是通过重新定义“是否读正文”关闭。其他宿主结果只备案。ZCode G5 缺管理 CLI 的因果分支仍未直接证明，已限定接受、不再补探针；PATH 限制曾被绝对路径绕过，不能用它伪称工具不可用。
- 7-B1 历史 10/10 与 S3／C2／Q1 三次终止尝试的分母透明度只登记，2026-09-14 方案 A 已接受结果不作废／重验。
- A2 深度资格边界不预设改判；688048.SH 未出现的零分母／状态转换不外推；B2 部分 transcript／用量轮转、M2-6 原组装输入不可恢复、容量可见／原生 compact／50KB 安全阈值未证的限制分别保留。不同能力的缺口不互相抵消。
- 2026-10-03 用户批准清理后，已移除 host_acceptance 的无引用常量、sweep 死赋值、“窗内重复”标记及恒真条件、native-interfaces 的无消费标签、扩展不可达分支和测试中 29 处重复环境设置；同时减少工作包重复解析、归档重复秘密过滤和三个未消费 DB 列的必需性门禁。安装维护的无调用方法与死赋值已删除，缺失路径改在构造 Path 前拒绝。原事务恢复、依赖判环、脱敏和计量归属要求保留；skill.__init__ 的 extensions_root 继续作为公开导出保留。
- 正常类型门禁为 `mypy src`；脚本历史 mypy 有 55 项既存诊断，不把全脚本清零变成功能交付条件或宣称已通过。比较中位数与比较表生成器当时在证据层，没有仓库实现，工具交付不替代性能口径证明。

历史提出而后关闭的 uninstall 越界、check 缺交付误通过、capture 覆盖及续行泄漏不再列未修；F1/F2 后续修复也从未修登记移出。其余可选清理、范围外认证与能力补齐不改变本期已接受结论，不授权实现或真实补验。
