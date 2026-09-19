# 河图研投助手

基于证据的 A 股个股分析 Agent Skill：用户用自然语言向宿主 Agent（ZCode、Codex、
Claude Code、OpenCode）发起
研究请求，canonical Skill 负责请求理解、研究规划、来源选择、失败处理、综合与最终
中文 Markdown 报告；`hetu-stock` 命令行只负责 Skill 包管理与可选的确定性辅助。

## 版本状态

| 项目 | 当前状态 |
|------|----------|
| 产品版本 | **V0.2（未正式发布；V1 未完成）** |
| 交付阶段 | V1 一至四期已交付；五期各阶段已执行或部分执行，阶段 07 综合验收**进行中、尚未最终验收**（现状汇总与剩余缺口见 `.hetu/validation/phase-5/20260917-stage07-evidence-summary/`） |
| 支持范围 | 单只中国 A 股 `security` |
| Python | 3.11、3.12 |
| 宿主验收（2026-09-14 质量门槛；2026-09-15 计量治愈） | **ZCode：standard×public 组合质量门槛已验收**（10 次独立端到端、10/10 质量通过、全部锁定与独立评分、关键主张追溯 100%）；计量维度（仅用量）经客户端 db durable 通道恢复——2026-09-15 当时检查口径的历史结果：回填 11 次中 6 次完全通过、5 次剩原运行非计量缺口（隔离/工件类）；计时端点缺口（hidden 通知 fail-closed 案例）与隔离限制（预立探针有限定口径）分别如实标注，不以用量恢复宣称完整任务计量成立。quick（6 次）/deep（1 次）有证据未达组合门槛；**Codex、OpenCode、Claude Code：功能层已验收**（安装发现、触发边界、扩展管理、接口探针，2026-09-13），完整研究 `UNVERIFIED`（延后，仅功能层认证）。四宿主目标保留，组合完整认证缺口如实标注，不称 V1 完成 |
| 性能验收（2026-09-17） | 三条性能验收线**均未正式通过**：全流程配对 0/3、0/1、0/1（端点新口径下仅 P1 可证，为优化前参考值）；并行独立域真实重叠已证、中位提速与工具 ≤ 基线未成立（单对窗口口径观察差异不构成验收，且该对不满足独立从零）；复用收益对照未测。当前性能数字均为所声明窗口内的观察结果，不支持正式端到端达标判定 |

版本变化见 [版本日志](CHANGELOG.md)。

## 研究证据入口（本地）

五期验收证据（组合级研究、触发矩阵、安装验证、批次记录）位于
`.hetu/validation/phase-5/`，**仅供本地使用**（含个人路径与未脱敏运行数据，
不作为共享材料；对外分享须先脱敏）。关键索引：

- 五期阶段 07 宿主验收矩阵与最小补验清单：`.hetu/validation/phase-5/20260912-stage07-host-acceptance/host-capability-matrix.md`
- zcode×standard 组合 10 次证据与批次记录：`.hetu/validation/phase-5/20260913-stage07-research-batch/batch-record.md`
- 阶段 07 现状汇总与剩余缺口清单（2026-09-17）：`.hetu/validation/phase-5/20260917-stage07-evidence-summary/status-summary.md`、`gap-list.md`
- 性能相关记录：并行专项 P1（`20260915-stage07-parallel-benefit/record.md`）、全流程配对盘点（`20260916-stage07-fullflow-pairing/record.md`）、顺序/并行对判定与订正层（`20260917-stage07-seqpar-pair/`）
- 宿主原生接口事实表：`tests/product/fixtures/host_cli/native-interfaces.json`

## 当前能力

研究执行权由 Agent 主导。用户用自然语言向宿主 Agent 发起请求：

```text
用公开数据标准分析 600519。
```

canonical Skill（`skills/hetu-stock-analysis/`）拥有完整的研究执行链路：请求理解、
研究规划、来源选择、失败处理、证据综合与最终 Markdown 报告。`hetu-stock` CLI 不
是 Agent，也不是 LLM 客户端，不生成研究事实，也不校验研究语义。

```text
用户请求（自然语言）
  -> 宿主 Agent（ZCode / Codex / Claude Code / OpenCode）+ canonical Skill：
       请求理解、研究规划、来源选择、失败处理、证据综合、最终 Markdown 报告
  -> hetu-stock CLI：Skill 包管理 + 可选确定性辅助（helper）
  -> 中文 Markdown 报告
```

`hetu-stock` 命令行只暴露两个顶层组：

- `hetu-stock skill`：管理 canonical Skill 包（`validate`、`install`、
  `status`、`diagnose`、`rollback`、`uninstall`）。
- `hetu-stock helper`：可选的确定性辅助命令（时点边界、授权检查）。helper 不可用
  时，公开研究仍可用宿主等价工具继续。

只读 legacy 兼容面已退场：`hetu_stock/{workflow,models,report,config}`、
`legacy_cli.py` 及其专属测试与 Jinja 依赖已删除，只能通过 Git 历史追溯。

### 授权失败语义

authorized 模式下，当某个授权来源失败时，只阻塞与该来源相关的数据，其余已授权
数据继续可用；运行保持 authorized 状态，直到用户做出显式决定（重试、切换公开、
终止等）。系统从不持久化已解析的 secret 列表--secret 只以 `secret://` 引用名形式
存在，解析结果属于每次调用的输入。

## 支持范围

当前只支持单只 A 股。用户可在宿主的自然语言 Agent 请求中直接提供明确公司名称、
6 位 A 股代码，或带大写 `.SH`、`.SZ`、`.BJ` 后缀的代码，例如 `600519`、
`600519.SH`、`000001.SZ`、`430047.BJ`。canonical Skill 在 W1 核对证券映射、发行人
和上市状态；多个合理候选会暂停并请用户选择。路径、Markdown/HTML 注入及无法唯一
界定的描述不会被静默猜测为证券。

行业、板块、多股票比较、组合和结构化交易动作暂不支持。

## 快速开始

### 1. 从 GitHub 获取

```bash
git clone https://github.com/coding-alchemy/HETU.git
cd HETU
```

需要预先安装 Git、Python 3.11 或 3.12，并确保 pip 能正常通过 TLS 下载依赖。

### 2. 一键安装 Agent Skill 和 Python 辅助工具

```bash
./scripts/install.sh --host codex
# 也可改为 --host claude、--host opencode 或 --host zcode
```

脚本支持 macOS/Linux，在用户目录创建按版本隔离的 Python 环境，并安装 canonical
Skill。已有 Skill 需要更新时显式增加 `--force`；更新采用原子目录交换，失败或中断
后旧版 Skill 与旧环境保持可用，可用 `hetu-stock skill diagnose` 查看状态、
`skill rollback` 回滚、`skill uninstall` 卸载。安装布局、PATH 和 TLS 排障见
[Agent Skill 安装与使用](docs/agent-skill-usage.md)。

如果机器上有多套 Python，可以显式选择，例如：

```bash
./scripts/install.sh --host codex --python python3.12
```

### 3. 在宿主中发起研究

安装后直接对 Agent 说：

```text
用公开数据标准分析 600519。
```

宿主 Agent 会读取安装后的 canonical Skill，使用当前已授权的搜索、浏览或数据工具
执行研究，并产出带引用的中文 Markdown 报告。Agent 自行报告研究过程与结论。

## 开发与质量门禁

源码开发环境使用 editable 安装：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

也可以从开发分支只安装 Python 辅助工具，但该命令不会安装 canonical Skill：

```bash
python -m pip install \
  "hetu-stock @ git+https://github.com/coding-alchemy/HETU.git@zn_dev"
```

```bash
bash scripts/check.sh
```

`scripts/check.sh` 是当前唯一的完整仓库门禁：它收集全部测试，运行 `product` 与
`helpers` 测试、Ruff、mypy、文档检查、Skill manifest 更新/无差异检查及 Skill 校验。
一期 `legacy` 与 `frozen` 测试已随旧源码一起删除。

默认运行是完全离线的工程门禁：不启动任何宿主或模型。需要复核已显式选定的证据
目录时，设置 `HETU_HOST_EVIDENCE=<观察目录>` 再运行同一脚本，会在测试之后追加
`scripts/host_acceptance.py check --host-evidence <观察目录>`（复用同一 check 入口，
结果写入该目录内新的 `host-support-check.json`，已存在则拒绝覆盖）。无论哪种运行，
工程门禁通过或单次 check 通过都不构成任何宿主／模型组合的正式支持认证；未认证
组合仍以版本状态表中的 `UNVERIFIED` 为准。支持记录
`host-support-record.json` 由 `run --authorization-ref <授权引用名，不含凭据>` 在授权与
能力验证通过后、执行 case 前写入观察目录（create-only；无授权引用或无已验证能力事实时
不写，下游 `check --host-evidence` 如实判“不支持”）。真实验收另有仅手动触发的
`.github/workflows/host-acceptance.yml`，凭据只经 GitHub Secrets 引用；其观察输入只有
两种受约束模式（claude `--probe`、zcode `--watch-agent` 规格），codex/opencode 或
未给观察输入时 `run` 以 exit 1 明确失败——尚缺组合保持明确未认证，不是静默跳过。

## 文档

- [Agent Skill 安装与使用](docs/agent-skill-usage.md)
- [授权数据源配置示例](config/data_sources.example.yaml)
- [版本日志](CHANGELOG.md)
- [中国股票分析体系全景指南](docs/theory/中国股票分析体系全景指南.md)
- [A股板块与行业分析指南](docs/theory/A股板块与行业分析指南.md)
