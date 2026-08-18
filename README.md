# 河图研投助手

基于证据的 A 股个股分析 Agent Skill：用户用自然语言向 Codex 或 OpenCode 发起
研究请求，canonical Skill 负责请求理解、研究规划、来源选择、失败处理、综合与最终
中文 Markdown 报告；`hetu-stock` 命令行只负责 Skill 包管理与可选的确定性辅助。

## 版本状态

| 项目 | 当前状态 |
|------|----------|
| 产品版本 | **V0.1（未正式发布）** |
| 交付阶段 | Agent 主导研究闭环已完成并经真实人工主链验证；报告模板加固与后续增强开发中 |
| 支持范围 | 单只中国 A 股 `security` |
| Python | 3.11、3.12 |
| 原生宿主验收 | Codex、OpenCode、Claude Code 均为 `UNVERIFIED`；Codex 已完成人工主链，但未完成正式全场景认证 |

版本变化见 [版本日志](CHANGELOG.md)。

## 当前能力

研究执行权由 Agent 主导。用户用自然语言向 Codex 或 OpenCode 发起请求：

```text
用公开数据标准分析 600519。
```

canonical Skill（`skills/hetu-stock-analysis/`）拥有完整的研究执行链路：请求理解、
研究规划、来源选择、失败处理、证据综合与最终 Markdown 报告。`hetu-stock` CLI 不
是 Agent，也不是 LLM 客户端，不生成研究事实，也不校验研究语义。

```text
用户请求（自然语言）
  -> 宿主 Agent（Codex / OpenCode）+ canonical Skill：
       请求理解、研究规划、来源选择、失败处理、证据综合、最终 Markdown 报告
  -> hetu-stock CLI：Skill 包管理 + 可选确定性辅助（helper）
  -> 中文 Markdown 报告
```

`hetu-stock` 命令行只暴露两个顶层组：

- `hetu-stock skill`：管理 canonical Skill 包（`validate`、`install`）。
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
# 也可改为 --host claude 或 --host opencode
```

脚本支持 macOS/Linux，在用户目录创建隔离 Python 环境，并安装 canonical Skill。
已有 Skill 需要更新时显式增加 `--force`。安装布局、PATH 和 TLS 排障见
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

## 文档

- [Agent Skill 安装与使用](docs/agent-skill-usage.md)
- [授权数据源配置示例](config/data_sources.example.yaml)
- [版本日志](CHANGELOG.md)
- [中国股票分析体系全景指南](docs/中国股票分析体系全景指南.md)
- [A股板块与行业分析指南](docs/A股板块与行业分析指南.md)
