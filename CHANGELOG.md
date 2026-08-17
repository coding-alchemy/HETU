# 版本日志

本文件记录 HETU 已实现并交付的产品功能；未实现的需求不属于版本日志范围。

## V0.1（未正式发布）

- Agent 主导研究闭环：用户用自然语言向 Codex 或 OpenCode 发起请求，canonical
  Skill 负责请求理解、研究规划、来源选择、失败处理、证据综合与最终带引用的
  中文 Markdown 报告；Codex 上的真实 002371.SZ 人工主链已验证产品达到
  “基本可用”。
- `hetu-stock` 命令行只暴露 `skill`（Skill 包管理与校验、安装）与 `helper`
  （可选确定性辅助：时点边界、授权检查）两个顶层组；`hetu-stock` 不是 Agent，
  也不是 LLM 客户端。
- authorized 模式下来源失败只阻塞相关数据，运行保持 authorized 直到用户显式
  决定；secret 仅以 `secret://` 引用名存在，解析结果与操作状态均为每次调用的
  显式输入，系统从不持久化已解析 secret。
- 一键安装：`scripts/install.sh` 支持 macOS/Linux，在用户目录创建隔离 Python
  环境并安装 canonical Skill 到 Codex、Claude Code 或 OpenCode；安装前后执行
  manifest 清单与 SHA-256 校验。
- 单只中国 A 股研究（名称、6 位代码或带 `.SH`/`.SZ`/`.BJ` 后缀代码），行业、
  板块、多股票比较与组合暂不支持。
- 正式宿主支持认证均为 `UNVERIFIED`：安装兼容性不等同于支持，当前仓库没有
  可执行的宿主认证流程。
- 完整门禁为 `bash scripts/check.sh`。
