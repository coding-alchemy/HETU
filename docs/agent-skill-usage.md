# Agent Skill 安装与使用

`hetu-stock` 提供可移植的 Agent Skill 包 `skills/hetu-stock-analysis/`，支持一键安装到主流 Agent 宿主。

## 快速开始

```bash
git clone https://github.com/coding-alchemy/HETU.git
cd HETU
./scripts/install.sh --host codex
```

将 `codex` 改为 `claude`、`opencode` 或 `zcode` 即可安装到其他宿主。已有 Skill 需要
更新时使用 `./scripts/install.sh --host <host> --force`；更新采用原子目录交换，旧版
保留为备份，新环境准备或切换失败时旧环境与旧 Skill 保持可用。由旧版安装器创建的
`hetu-stock/venv` 布局会被安全识别并保留为上一可用组合，可直接 `--force` 升级。

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

- `hetu-stock skill`：管理 canonical Skill 包。`validate`、`install` 负责校验与
  安装；`status`、`diagnose` 为只读诊断（完整性、备份、受管启动器、辅助环境与
  下一动作，不联网、不修复）；`rollback` 回滚到最近一份完整备份并保留被换下版本；
  `uninstall` 先列出处理范围、经 `--yes` 确认后只删除受管 Skill、受管启动器及
  （可选且不被共享的）辅助环境，用户文件、其他宿主、研究与授权配置保留。
- `hetu-stock helper`：可选的确定性辅助命令（时点边界、授权检查、旧成果读取）。
  helper 不可用
  时，公开研究仍可借助宿主等价工具继续。`archive-inspect --source <旧目录>`
  只读检查旧格式（schema 3）研究目录，逐字段呈现请求、证据/主张、论点、阶段尝试、
  原采用和用户决定，未知字段原样保留、源字节不变；`archive-export --source <旧目录>
  --output <新目录>` 导出可读索引与授权允许的副本（含疑似秘密的原记录不复制、原件保留
  原位；秘密过滤限已支持的 JSON 字段）。读取旧成果不会启动或恢复研究。

一期只读 legacy 兼容面已退场：旧 workflow、models、report、config 源码、
`legacy_cli.py` 及其专属测试与 Jinja 依赖已删除，只能通过 Git 历史追溯
（历史阶段编号下的退场记录）。

`hetu-stock` 不是 Agent，也不是 LLM 客户端，不生成研究事实，也不校验研究语义。

五期中期（功能复评通过，十二条要求在已批准范围内闭环）为 Skill 增加两项研究行为能力：任务控制与恢复（取消后
不再发起新调用、迟到返回未经恢复不采用、调整只动剩余工作、提前交付仍完成两轮自检、跨会话
恢复先筛选候选再按需读取检查点）和资料复用与产物追溯（`reuse_previous_task_data` 默认开启，
适用旧材料复制为本地副本并记录来源任务、原文件、原始来源与两个时间；显式关闭时不查找、
不读取旧任务资料，获取失败不回退）。这些行为的已有证据来自受控合成样本、ZCode 桌面端
协调会话与 Flash 机制内容级补证；未覆盖的宿主组合不宣称支持，详见
`specs/2026-09-21-stock-analysis-workflow-v1-phase-5-middle-implementation.md`。

五期下一批迁移（全面评审及 R1 复评通过，待用户合并 main）为 Skill 增加三组研究行为能力：长材料分批读取
与整理（按问题控制单批字节量，分批不丢例外、否定与条件，压缩或转交保留证券、时点、授权、
来源、计算、事实、冲突、缺口与用户决定，只减少已证明重复的读取）、正式子任务并行与汇合
（两个以上独立问题才评估并行，自足输入、最小权限与隔离写入，汇合裁决去重同源、统一单位、
冲突回源，局部失败不波及其他研究）和三项减少重复工作的优化（任务内共享取数只取一次、
充分成果不重复研究、最终报告呈现先于验收锁定与锁后评分）。既有证据来自受控合成样本与
受控短链路；宿主原生压缩、容量可见路径与真实并行收益未验证的边界继续披露，详见
`specs/2026-09-24-stock-analysis-workflow-v1-phase-5-execution-implementation.md`。

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
| Python 辅助工具隔离环境（按版本独立） | `${XDG_DATA_HOME:-$HOME/.local/share}/hetu-stock/envs/env-<时间戳>-<pid>` |
| 当前组合记录 | `${XDG_DATA_HOME:-$HOME/.local/share}/hetu-stock/installation.json` |
| CLI 启动器 | `$HOME/.local/bin/hetu-stock`（指向当前版本环境的符号链接） |
| Skill 暂存／备份／事务记录 | 目标 skills 根目录外的受管区 `<skills 上级>/.hetu-skill-maintenance/<目标摘要>/`，宿主 Skill 发现不会读取该目录 |
| canonical Skill 源 | 当前仓库的 `skills/hetu-stock-analysis` |

每次维护在最终保留路径新建独立版本环境，完成依赖安装与 CLI 自检后才切换启动器；
成功后只保留当前与上一可用组合，供回滚使用。脚本通过绝对启动器完成自检，因此
`~/.local/bin` 不在 PATH 时安装仍然有效。若宿主无法从 PATH 找到 `hetu-stock`，
Skill 会改用 `$HOME/.local/bin/hetu-stock`。

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
| ZCode | `~/.zcode/skills`（依据原生发现证据核对） |

每个宿主安装后的目录名均为 `hetu-stock-analysis`，包含 `SKILL.md`、引用文档和报告撰写指引。

### 诊断、回滚与卸载

```bash
# 只读查看安装状态（完整性、备份、启动器、辅助环境）
hetu-stock skill status --host claude
hetu-stock skill diagnose --host claude

# 回滚到最近一份完整备份（被换下版本保留为备份）
hetu-stock skill rollback --host claude
# 或指定 diagnose 列出的备份目录
hetu-stock skill rollback --host claude --backup <维护区备份目录>

# 卸载：先列出处理范围，确认后加 --yes；--remove-env 仅在无其他宿主使用时删除环境
hetu-stock skill uninstall --host claude
hetu-stock skill uninstall --host claude --yes
```

这些维护命令只操作受管目录与受管启动器，不删除宿主的其他配置、研究资料、旧报告、
授权配置或非受管文件。Skill 发布与启动器切换各自原子；跨目录的整套维护不是单个
原子操作，任一步失败会恢复已切换的部分，旧版在此之前不会被删除。

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

卸载使用 `hetu-stock skill uninstall --host <host> --yes`（见上文），安装器不会删除
非受管文件，也不会删除整个宿主配置目录。覆盖安装的原子目录交换已实测
macOS/APFS（真实交换、并发串行化、中断后恢复及不支持交换的负例）；Linux/ext4
与其他文件系统的实测留待五期后半补齐，未实测前不把对应平台记为已支持。

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
