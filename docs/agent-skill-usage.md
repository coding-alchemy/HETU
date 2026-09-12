# Agent Skill 安装与使用

`hetu-stock` 提供可移植的 Agent Skill 包 `skills/hetu-stock-analysis/`，支持一键安装到主流 Agent 宿主。

## 快速开始

```bash
git clone https://github.com/coding-alchemy/HETU.git
cd HETU
./scripts/install.sh --host codex
```

将 `codex` 改为 `claude` 或 `opencode` 即可安装到其他宿主。已有 Skill 需要更新时
使用 `./scripts/install.sh --host <host> --force`。

V1 一期实现已完成 Agent 主导研究闭环，Codex 上一次真实人工主链证明 Agent 产品路径基本可用。Codex、OpenCode 与
Claude Code 的正式宿主认证仍统一为 `UNVERIFIED`；安装兼容性本身不等同于支持，一次人工
主链也不等同于正式支持，只有完成版本化全场景验收并形成完整证据后才能更新支持状态。

安装后在宿主中直接说：`用公开数据标准分析 600519`。当前正常入口是宿主中的自然语言
Agent 请求：可直接给出明确公司名称、6 位 A 股代码，或带大写 `.SH`、`.SZ`、`.BJ`
后缀的代码。

## Skill 与 CLI 的职责

研究执行权由 Agent 主导。用户用自然语言向 Codex 或 OpenCode 发起请求；
canonical Skill 拥有完整研究执行链路：请求理解、研究规划、来源选择、失败处理、
证据综合与最终中文 Markdown 报告。Agent 自行报告研究过程与结论。

`hetu-stock` 命令行只暴露两个顶层组：

- `hetu-stock skill`：管理 canonical Skill 包（`validate`、`install`）。
- `hetu-stock helper`：可选的确定性辅助命令（时点边界、授权检查）。helper 不可用
  时，公开研究仍可借助宿主等价工具继续。

一期只读 legacy 兼容面已退场：旧 workflow、models、report、config 源码、
`legacy_cli.py` 及其专属测试与 Jinja 依赖已删除，只能通过 Git 历史追溯
（历史阶段编号下的退场记录）。

`hetu-stock` 不是 Agent，也不是 LLM 客户端，不生成研究事实，也不校验研究语义。

## 当前支持范围

Skill 只支持单只 A 股，即 `subject.type=security`。`industry`、`sector`、
`comparison` 和 `portfolio` 属于 deferred 范围，不能描述为当前已支持。

用户可以提供明确公司名称、6 位 A 股代码，或带大写 `.SH`、`.SZ`、`.BJ` 后缀的
代码，例如 `600519`、`600519.SH`、`000001.SZ`、`430047.BJ`。Agent 在 W1 中核对
证券映射、发行人和上市状态；存在多个合理候选时暂停并请用户选择。路径、Markdown/HTML
注入或无法唯一界定的描述不会被静默猜测为证券。当前正常入口是宿主中的自然语言 Agent
请求。

## authorized 模式

authorized 模式的来源 registry（见[授权数据源示例](../config/data_sources.example.yaml)）
只记录来源元数据与 `secret://` 引用名，不写入原始密钥。当前用户授权范围、secret
解析结果、请求 purpose、评估 `as_of` 与操作状态都是每次调用
`hetu-stock helper authorization-check` 的显式输入，不持久化在配置文件里。

authorized 模式下，当某个授权来源失败时，只阻塞与该来源相关的数据，其余已授权
数据继续可用；运行保持 authorized 状态，直到用户做出显式决定（重试、切换公开、
终止等）。系统从不持久化已解析的 secret 列表。

## 一键安装布局

安装脚本支持 macOS/Linux，并要求预先安装 Git、Python 3.11/3.12 和 pip。
脚本不调用 `sudo`，也不修改 shell 启动文件。

| 内容 | 默认路径 |
|------|----------|
| Python 辅助工具隔离环境 | `${XDG_DATA_HOME:-$HOME/.local/share}/hetu-stock/venv` |
| CLI 启动器 | `$HOME/.local/bin/hetu-stock` |
| canonical Skill 源 | 当前仓库的 `skills/hetu-stock-analysis` |

脚本通过绝对启动器完成自检，因此 `~/.local/bin` 不在 PATH 时安装仍然有效。若宿主
无法从 PATH 找到 `hetu-stock`，Skill 会改用 `$HOME/.local/bin/hetu-stock`。

## Skill 更新与自定义安装

```bash
# 在仓库根目录中使用默认 canonical source
hetu-stock skill install --host claude
hetu-stock skill install --host opencode
hetu-stock skill install --host codex

# 指定自定义 source 或目标目录
hetu-stock skill install --host claude \
  --source /path/to/skill \
  --destination /path/to/skills

# 强制覆盖已有安装
hetu-stock skill install --host claude --force
```

### 默认安装路径

| 宿主 | 默认路径 |
|------|----------|
| Codex | `$CODEX_HOME/skills` 或 `~/.codex/skills` |
| Claude | `~/.claude/skills` |
| OpenCode | `$XDG_CONFIG_HOME/opencode/skills` 或 `~/.config/opencode/skills` |

每个宿主安装后的目录名均为 `hetu-stock-analysis`，包含 `SKILL.md`、引用文档和报告撰写指引。

## 常见安装问题

如果 pip 报告 `CERTIFICATE_VERIFY_FAILED`，说明当前 Python 的 CA 证书或网络代理配置
不能验证 GitHub/PyPI 的 TLS 证书。应修复 Python/操作系统证书配置；不要使用
`--trusted-host` 或关闭证书校验。

如果另一套受支持的 Python 证书配置正常，可以显式选择它：

```bash
./scripts/install.sh --host codex --python python3.11
```

如果终端找不到 `hetu-stock`，可以直接运行：

```bash
$HOME/.local/bin/hetu-stock --help
```

也可以自行将 `$HOME/.local/bin` 加入 PATH。安装脚本不会自动修改 `.profile`、
`.bashrc`、`.zprofile` 或 `.zshrc`。

当前安装器不提供自动卸载命令。需要卸载时只可移除上述受管 venv、启动器和所选宿主的
`hetu-stock-analysis` Skill 目录；安装器不会删除非受管文件，也不会删除整个宿主配置
目录。Skill 覆盖仍为非原子语义，备份和失败回滚属于 V1 安装治理增强。

## 开发安装

仓库贡献者使用：

```bash
python -m pip install -e '.[dev]'
```

以下命令只安装开发分支上的 Python 辅助工具，不会安装 canonical Skill：

```bash
python -m pip install \
  "hetu-stock @ git+https://github.com/coding-alchemy/HETU.git@zn_dev"
```

## 宿主路径验证记录

以下路径在 2026-07-17 基于官方文档与本机环境核对：

| 宿主 | 本机版本 | 验证的 Skill 根目录 | 环境变量覆盖 | 验证日期 |
|------|----------|---------------------|--------------|----------|
| Codex | codex-cli 0.142.5 | `~/.codex/skills`（本机已存在） | `CODEX_HOME`（当前未设置） | 2026-07-17 |
| Claude Code | 2.1.211 | `~/.claude/skills`（本机已存在） | 无 | 2026-07-17 |
| OpenCode | 1.17.20 | `~/.config/opencode/skills`（本机已存在） | `XDG_CONFIG_HOME`（当前未设置） | 2026-07-17 |

## 包完整性校验

安装命令会在复制前读取 `skills/hetu-stock-analysis/MANIFEST.json`，执行两项检查：

1. **文件清单覆盖**：包内除 `MANIFEST.json` 外，所有文件必须在清单中列出。
2. **SHA-256 校验**：每个文件的实际哈希必须与清单一致。

任一检查失败都会中止安装，避免不完整的 Skill 包被加载。

## 手动安装

也可以直接复制：

```bash
cp -r skills/hetu-stock-analysis ~/.claude/skills/hetu-stock-analysis
```

复制后建议用 CLI 校验：

```bash
hetu-stock skill validate ~/.claude/skills/hetu-stock-analysis
```

## 报告生成

安装 Skill 后，Agent 在研究完成后直接产出带引用的中文 Markdown 报告；报告由
canonical Skill 负责综合与撰写，不再由 `hetu-stock` 命令行渲染。产品路径不调用
任何渲染命令。authorized 来源失败时只阻塞相关数据，
运行保持 authorized 直到用户显式决定；public 研究在 helper 不可用时仍可借助宿主
等价工具继续。

## 五期观察入口（阶段 01，尚未认证）

`scripts/host_acceptance.py` 提供确定性计量与宿主观察入口，供五期验收使用；普通研究不需要调用它。

```bash
python scripts/host_acceptance.py probe --host zcode --output <新的观察目录>
python scripts/host_acceptance.py run  --host <host> --case <研究侧 case.json> --output <新的观察目录>
python scripts/host_acceptance.py sweep --evidence <已有观察目录> --session <协调会话 sess_*>
python scripts/host_acceptance.py check --evidence <已有观察目录> --output <结果.json>
```

- `probe` 只核对原生能力（版本、观测目录、metadata usage 样例、可选的带标注实况小探针），不分析股票；`host` 取 `codex|claude|opencode|zcode`。能力不成立时退出非零并列出缺口。ZCode 另有 `--isolation` 模式：对协调层按受控诱饵程序派发的两个子代理（受限例＋放任反例）做转录扫描，生成只含布尔、计数与判定依据的最小隔离证据（受限例 hold、放任例须检出、初始加载/历史注入/资料访问三项控制全部 verified 才判 pass）；`--staging-dir` 可核验轮转前快照。
- `run` 只校验并登记单个显式 case（证券、中性请求、时点、模式、深度、复用开关、获准材料），拒绝任意 shell 内容与评审答案；真实派发由协调上下文按 case 执行，脚本不决定研究下一步。ZCode attach 模式以 `--watch-agent <原生id>=<scope>`（可重复，scope 必须显式）同时观察多个会话：子代理按 metadata 解析、`sess_*` 直观察协调者会话、`file:<路径>=<scope>` 观察"日志轮转前快照"。采集按完整行推进游标、退前排空、轮转/截断/坏行/非 completed 终态均写入 `collection-gaps.jsonl` 并按代数分段；工具事件只保留 id/name/分类（wrapper 派发单列），正文、参数与凭据不落盘。`--request-at`/`--delivered-at` 记录真实用户请求与协调交付时点。
- `check` 离线复核观察目录中的用量与计时：按来源会话与消息 id 去重、累计快照按段压缩（起始快照按基线相减）、缓存口径未知不归一、完整性逐指标判定（输入/输出任一为 null 即不完整）、截断行与 collection gap 即失败、非 probe 运行必须声明至少 research＋review 必需范围；隔离证据须实际存在、可解析、对应本任务并满足两案例与三项控制要求。passed=false 即证据不完整；交付端点未独立观察（coordinator-reported/last-scope-completed）、实际发生而未声明的 scope、隔离证据未覆盖的参与上下文均判失败，探针通过与完整基线资格（baseline_qualification）分开输出。
- `run --delivery-sweep`（2026-09-09 新增）：交付回合发生在 `run` 退出之后，run 自身永远观察不到。该模式要求恰好一个 `<id>=coordination` 观察规格且不给 `--delivered-at`；收尾时不记交付缺口，改记 `delivery_source=pending-sweep` 并保存协调会话游标（`sweep-state.json`）。协调者在交付后（观察目录内写入交付标记文件，默认 `delivery-message.md`）的下一回合运行 `sweep --evidence <目录> --session <协调会话>`：sweep 消费 rollout 尾部全部完整记录并一次性盖章 `delivery-observation.json`，端点取最后记录的原生 `completedAt`；标记 mtime 只用于核验"交付先于 sweep"（晚于端点即失败），不用于切分记录——一个回合跨多个 API 调用，按标记切分会把交付回合自己的尾部丢掉。因此 sweep 只能保守地多计（拖晚 sweep 只会增大端点与总量，不会漏计交付回合），交付后应尽早在下一回合执行 sweep；重跑 sweep 拒绝，端点不会漂移。`check` 遇 pending-sweep 时必须能解析该观察（缺文件、标记缺失或晚于端点、会话不符均失败），通过后以观察端点闭合请求→交付区间。live 模式按 sweep-state 游标续读（游标经 JSON 往返的 identity 强制回元组、推进后原地原子回写，均为 2026-09-10 修复——此前 live sweep 带存储游标必然误报 rotated 或无法回写）；`--transcript` 快照模式从字节 0 消费并靠身份去重，快照前已发生的轮转丢失不可检测，快照须紧贴交付。
- `run --watch-control <文件>`（2026-09-10 新增）：运行中按同源游标语义轮询 JSONL 控制文件。`{"op":"watch","id":"<原生id>","scope":"<scope>"}` 动态注册晚现会话（核对/修正上下文在研究完成后才派发，id 无法在 run 启动时预知）；`{"op":"finalize"}` 声明交付收尾——在此之前 run 即使全部当前 watcher 已结束也不退出，持续等待新会话并增量采集协调转录，finalize 后按原静默规则排空退出。expected_scopes 覆盖全部曾加入的 watcher，单一证据目录声明整个任务。fail-closed：坏行、非法/冲突/finalize 后的注册、delivery-sweep 下追加第二个 coordination、无 finalize 超时、控制文件自身轮转/截断或退出时残留半行，均写入 `collection-gaps.jsonl` 并使 `check` 失败。
- `run --resume` 与 `status`（2026-09-10 基础设施修复新增）：run 每个变更周期把全部 watcher 游标、控制游标与 request_at 持久化到 `watch-state.json`（首循环即持久化）；采集器被外部终止后，`run --resume`（同目录、省略 `--watch-agent`、其余旗标同原启动）从保存状态续跑，死窗期间发生的轮转/截断按既有语义记缺口——损失被界定为死窗内未消费字节而非整个任务窗。`status --evidence <目录>` 为只读视图（工件、逐 scope 事件数、缺口、watcher 游标、agent 元数据状态与转录空闲时长），用于区分正常长调用与代理停摆。同批修复：file: watcher 与隔离扫描的 session_id 统一为原生会话 id（去 `model-io-` 前缀），live agent watcher＋隔离验证组合自此可通过 check。

已验证（2026-09-08，第三轮修复后）：ZCode 受控链路探针（非股票分析）四 scope（研究/核对/修正/协调）全程采集可用；修正判定后交付尾部为已知缺口，结果如实为“小计＋缺口”（passed=false，非完整计量），证据见 .hetu/validation/phase-5/20260908-stage01-fix3/chain-probe/。隔离证据按旧任务数据边界核验研究/核对/修正三个参与上下文的初始加载、历史注入与资料访问（空/损转录不证隔离、越界访问即失败），verdict=pass，证明范围限于受控种子标记设置。B04 核对事件回放与审计值精确一致（33,319 输出 token、46 个工具调用、缓存读取 1,279,616 与写入 0 分列）。**宿主限制（重要）**：claude 无头模式实测无可用路径级隔离（证据见 .hetu/validation/phase-5/20260908-stage01-fix/probe-isolation/），且其观察器按 mtime 初选转录、未绑定启动进程的原生会话 id，可能采到并发任务会话——这两点使 claude 当前不满足完整计量与隔离前提；Codex/OpenCode 未接入。ZCode 隔离无法观察未知自动注入机制，**每次真实采样须重立隔离证据**。**轮转风险（重要）**：本机 rollout 为全局小轮转池（约 3 个文件）且被并发会话共享——采集必须紧贴任务执行，或先用 stage-agent-transcript.sh 快照再以 file: 规格观察；快照是含完整请求的工作副本，仅存放 /tmp 并在采集后删除，证据只保留白名单字段与布尔判定。以上均不能视为宿主正式支持认证。

已验证（2026-09-09，02 前置修复后）：`run --delivery-sweep` ＋ `sweep --transcript` 在真实运行形态（probe_mode=false）达成 `check` `passed=true` 且 `baseline_qualification=established`——交付端点取自协调会话原生记录、请求→交付区间完整闭合、零采集缺口，证据见 .hetu/validation/phase-5/20260909-stage02-calibration/chain-probe-sweep-r3/（三轮迭代与失败轮次记录见同目录 probe-record.md：轮转丢尾由 fail-closed 拦截、快照过早由时序判定拦截，最终以"交付回合只写标记、下一回合先快照再 sweep"固化操作合同）。修复前该宿主计量只能输出"小计＋缺口"，基线资格结构上不成立；修复仅新增交付终点观察，不改研究语义与既有结果。claude／Codex／OpenCode 的限制与未接入状态不变。

已验证（2026-09-10，基础设施修复后）：`run --resume`＋`watch-state` 持久化使被外部终止（含 `kill -9`）的采集器可在同一观察目录续跑，损失界定为死窗内未消费字节（离线 V7a 干净恢复后单目录 established、V7b 死窗轮转损失 2 条且 fail-closed）；真实形态探针 **r5** 完整复演 S2 事故场景（研究 agent-kind watcher live 挂载→采集器 kill -9→status 确认→resume→核对/修正动态 agent 注册→隔离原生 id 覆盖→finalize→live sweep）达成 `passed=true`／`established`／零缺口、独立复算一致（.hetu/validation/phase-5/20260910-stage02-collection-verify/chain-probe-r5/，实现与验证见同目录 fix2-record.md）。单测套件 102/102。遗留边界如实：采集器死亡且协调层长时间未察觉期间无保护（S2 约 70 分钟即此情形，缓解为 status 尽早发现＋立即 resume）；协调会话文件池压翻代在 live 期间损失有界、死窗期间按缺口呈现。

已验证（2026-09-10，动态注册修复后）：`run --watch-control` 在真实派发顺序（研究完成→等待→核对加入→修正加入→finalize 交付收尾）下以**单一证据目录**达成 `check` `passed=true` 且 `baseline_qualification=established`、独立复算一致、等待期协调记录实时落盘；无 finalize 超时与等待期轮转均 fail-closed。离线进程级验证（V5/V6）见 .hetu/validation/phase-5/20260910-stage02-collection-verify/（缺口证实 V1–V4、实现与顺带缺陷修复见 fix-record.md）；**真实形态**经链路探针 r4 复验（真实会话、真实派发顺序、live sweep 游标续读首次真实执行成功，证据见同目录 chain-probe-r4-record.md）。此前结论需相应修订：r3 探针通过仅覆盖"全部会话 id 在 run 启动时已知"的形态（其 review 代理先派发内部等待）；live sweep 带存储游标的两个缺陷为该日修复，r1 的"rotated"拒绝不能反推当时确有轮转。S1 质量结果与其余既有证据的有效性不变。

已验证（2026-09-11，阶段 03 合同落地）：研究 Skill 新增四组合同并经静态测试与受控行为样本验证（证据见 .hetu/validation/phase-5/20260911-stage03-execution/）。①任务关系与复用：`manifest.run` 条件字段 task_id/parent_task_id/reuse_previous_task_data（缺省＝未记录，旧运行不补写、不失效）；条目 provenance 五键（source_task_id/source_artifact/original_source/original_acquired_at/copied_at，source_artifact 仅结构校验——拒绝绝对路径与 `..`，不访问来源任务）；reuse=false 与 provenance 并存判显式矛盾；checker 不推断用途等价（两次尝试、多支持原文不判重复）。②预算与控制：三档预算为参考值（待校准，禁止当已批准默认）；同类重试计数到限换路/缩小/留缺口；七类停止条件各自停止对应工作；超时按八字段模板继续不暂停；取消后迟到返回一律未采用；恢复先五维筛选、真实歧义才问、false 不查旧任务、不重做无影响已完成项（C01–C10 全过）。③能力与长材料：host-tools 固化能力预检（未知版本只记能力）、独立核对入口（修正后只复查直接影响）、字节预算参考值分批推进、长材料七维度版本化＋压缩转交必留清单（关键否定移除评审判 FAIL 负例、容量未知拒绝编造实证）（L1–L3 全过）。④默认复用：`reuse_previous_task_data` 默认 true，"不用旧任务数据，从头分析"映射 false；适用旧材料复制为本地副本（五键 provenance、非链接、不整套复制、不伪装新下载）；false 零读取旧任务；未许可只留定位与限制、复制中断不登记成功（R01–R03 全过，R01 旧目录隔离后本地副本仍闭合）。

已验证（2026-09-12，阶段 04 合同落地）：研究 Skill 新增并行派发与汇合裁决两组合同并经静态测试与受控行为样本验证（证据见 .hetu/validation/phase-5/20260912-stage04-parallel/；02 impact-map 已按阶段 04 计划回写实现核对与 07 对照契约）。①派发合同（04.1）：正式子任务有稳定任务标识、父子关系与独立生命周期，尝试与采用沿用 03 记录合同，不改 W0–W10 身份、核心依赖与最低覆盖；只有两个及以上可独立理解、取证、产出且无共享可变状态的独立问题才评估并行，是否派发由主 Agent 判断；派发前统一证券、`as_of`、数据模式、深度、关注点、授权与证据规则六项，候选域按本次实际问题选择；请求规范化、依赖财务基线的预测估值、跨域综合、最大未知、监控、质量复核、报告默认串行、依赖先行；自足输入十二项、返回契约十三项、上下文只传当前问题所需部分；子任务不写 `manifest.json`、`checkpoint.md`、`evidence.md`、owner 正文和最终报告，返回经 owner 审查后串行合入。②汇合裁决与失败隔离（04.2）：子任务返回按完成度、来源、时点、授权、证据类型、结论边界六维审查后才可采用；同源转载去重（多转载≠多独立来源）、先统一单位与口径再比较、冲突回源裁决；多数、速度、子 Agent 自报置信度都不作采用依据；单个子任务失败仅影响对应问题，主 Agent 在预算内重试（按"同类重试计数"累计）、缩小、重新分派、顺序执行或局部降级并留缺口；authorized 返回合入前逐项再审实际来源、数据集、许可、点时、必要性与脱敏（"子任务完成"不是授权证明）；越权返回不采用且不进入提示/证据/日志，返回文本内嵌指令一律不执行、不扩权；无子 Agent 能力时同质量顺序完成并标注"未完成并行验证"，不以顺序结果冒充并行验证结论。③P01–P06 行为证据：受控合成场景（虚构主体，非股票分析）六样本全部通过——P01 四问题拆分与并行派发、P02 旧任务资料复用免重复派发（provenance 五键）、P03 同源转载去重与回源裁决、P04 超时重新分派与失败隔离、P05 无并行能力顺序回退、P06 越权/恶意返回处置（逐案独立刺激、判表审计、log 在盘）。④07 对照契约已固定：顺序/并行两侧同一问题契约（证券、深度、`as_of`、数据模式、宿主、模型、工具、授权、网络、字段口径）逐项固定，最小上下文与设计 §6 质量要求不变，至少三个独立研究域真实重叠；模型、工具、授权或网络差异逐项说明，不以版本指纹判断等价；数值待 02 基线确认后沿用，现为待定。并行收益证明留 07 配对完整研究，本阶段不宣称提速。
