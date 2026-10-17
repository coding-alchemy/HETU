# 河图研投助手

基于证据的 A 股个股分析 Agent Skill。用户向 ZCode、Codex、Claude Code 或 OpenCode
发起自然语言请求，由 canonical Skill 组织研究，产出带引用的中文 Markdown 报告。

当前版本为 **V0.3，尚未正式发布**。本次交付为 **V1 七期：分层优化与 Quick／Standard 能力补全**。

## 当前能力

- 个股研究：规划研究、选择来源、核对证据并综合成报告，支持任务调整与恢复、资料复用。
- `quick`：三年及最新期财务、业务构成、股东、事件、行情指标与图表；允许计算和客观变化描述。
- `standard`：完整继承 Quick 数据与图表，完成主要经营、财务质量、行业风险、三情景、基础估值、反证与复查研究，使用十二章报告。
- `hetu-stock skill`：安装、校验、诊断、回滚和卸载 Skill。
- `hetu-stock helper`：提供可选的时点边界、授权检查、旧成果检查与导出辅助；不可用时，公开研究可用宿主等价工具继续。

研究由宿主 Agent 执行，命令行只暴露两个顶层组：`skill` 和 `helper`。

三档需求、设计、实现、计算口径、报告合同与验收边界统一见[七期实现：分层优化与能力补全](specs/2026-10-12-stock-analysis-workflow-v1-phase-7-implementation.md)。
Deep 的原有研究能力与有效成果保持，本批次仅整理新目标及长期缺口。

## 支持范围

仅支持单只中国 A 股，可提供明确公司名称、6 位代码或带 `.SH`、`.SZ`、`.BJ` 后缀的代码，
例如 `600519`、`000001.SZ`、`430047.BJ`。存在多个候选时，Agent 会请用户确认。
行业、板块、多股票比较、组合和交易动作暂不支持。

ZCode 仅在已验收组合内获得研究认证；Codex、OpenCode、Claude Code 的完整研究仍为 `UNVERIFIED`。
具体范围与限制见[支持与验收记录](specs/2026-10-02-stock-analysis-workflow-v1-phase-5-implementation.md#phase5-status)。

## 快速开始

### 1. 获取项目

需要 Git、Python 3.11 或 3.12。

```bash
git clone https://github.com/coding-alchemy/HETU.git
cd HETU
```

### 2. 安装

```bash
./scripts/install.sh --host codex
# 也可选择 --host claude、--host opencode 或 --host zcode
```

脚本会安装 Skill 和 Python 辅助工具。更新已有安装时增加 `--force`；指定 Python 可使用：

```bash
./scripts/install.sh --host codex --python python3.12
```

脚本支持 macOS/Linux；安装维护已在 macOS/APFS 验证，Linux/ext4 尚未实测。
安装、更新与排障详见[安装与使用指南](docs/agent-skill-usage.md)。

### 3. 发起研究

在宿主中直接对 Agent 说：

```text
用公开数据标准分析 600519。
```

Agent 使用当前已授权的搜索、浏览或数据工具完成研究，并交付报告。

## 文档

- [Agent Skill 安装与使用](docs/agent-skill-usage.md)
- [授权数据源配置示例](config/data_sources.example.yaml)
- [版本日志](CHANGELOG.md)
- [Standard 成本分析与优化经验](docs/engineering/standard-token-cost-lessons.md)
- [中国股票分析体系全景指南](docs/theory/中国股票分析体系全景指南.md)
- [A股板块与行业分析指南](docs/theory/A股板块与行业分析指南.md)
