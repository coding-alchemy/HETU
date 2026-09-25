# 阶段 05：全面评审 4 项迁移前阻断问题修复

> 文档版本：v1.1
> 文档状态：已批准（2026-09-25 用户批准，含两项取舍：缺宿主保留捕获时非零退出、
> claude 夹具显式移除 `--permission-prompt-tool`）并执行完成
> 创建／修订日期：2026-09-25（v1.1：登记批准与执行结果）
> 范围：全面评审确认的 4 项迁移前阻断问题（均属原第 5、6 组组内缺陷修复，不新增需求组）：
> 卸载越界删除、check 计时 fail-open、capture 覆盖有效夹具、帮助文本描述续行误取。
> 依据：[修复设计 v1.2 §9–§10](../../2026-09-24-zn-dev-remaining-groups-fix-design.md)、
> [后半需求去向 §4 第 8 项](../../2026-09-20-stock-analysis-workflow-v1-phase-5-remainder-requirements.md)、
> 用户 2026-09-25 评审结论（4 项，附已证场景与要求）。
> 前序：阶段 01–04 已完成，其结论保持有效，本阶段不重开。

## 1. 范围与文件

实现：`src/hetu_stock/skill/extensions.py`（uninstall 边界校验）、
`scripts/host_acceptance.py`（check 计时完整性判定）、
`scripts/capture_host_cli.py`（缺宿主保留有效夹具、`_observed_flags` 声明/续行区分）。
测试：`tests/product/skill/test_extensions.py`、
`tests/product/validation/test_host_acceptance.py`、
`tests/product/validation/test_capture_host_cli.py`、
`tests/product/validation/test_host_cli_protocol.py`（仅当断言需随名单处置核对时）。
夹具：`tests/product/fixtures/host_cli/claude.json` 仅一处显式变更——从
`accepted_flags` 移除 `--permission-prompt-tool`（依据见设计 §9.4：唯一证据为描述续行，
未获证明；不迁入 `checked_absent`，不猜测支持/不支持）。

不改动：main、AGENTS.md、真实登记表、真实宿主与宿主配置、`.hetu/` 原始证据、workflow、
db-delivery、其余登记级后续项（设计 §4 第 8 项中未列为阻断的部分）。不重开 M2-6、
中期 B1–B6、已迁第 1–4 组；不启动股票分析、真实宿主会话、模型补验、认证或性能采样；
不使用 worktree/stash；不推送、不合并、不迁移。

前置核实：执行前确认分支 `zn_dev`、HEAD 与工作区状态；仅身份变化（提交压缩、状态订正）
不触发重验，有影响本批的语义变化则先列出报告。

## 2. 问题 → 修复 → 验证映射

| # | 已证场景 | 最小修复 | 行为验证（先红后绿） | 正向回归 |
|---|---|---|---|---|
| 1 | 登记表键被篡改为外部绝对路径后，uninstall 递归删除外部目录（extensions.py:736-737） | 删除与写回前校验 `_EXTENSION_ID` 形态＋`(root/id).resolve()` 受管边界，非法抛 ExtensionError | tmp 沙箱受管根＋根外哨兵；断言非零退出、哨兵与登记表字节不变、其他宿主绑定不变 | 正常卸载成功；仍有宿主绑定时拒绝（既有 :501/:515 用例） |
| 2 | delivered_at=None → complete=False、total_wait=None、gaps=[] 仍 passed=True、established（host_acceptance.py:4131-4133） | complete=False 且无具体 gap 时追加可定位失败，指明 request_at/delivered_at 缺失；不补造时间、不以零代未知 | 合成证据辅助构造缺 delivered_at：非零退出、not_established、原因可定位 | 完整合法基线通过；既有 timing gap 类型路径不变；probe 完整计时通过，probe 缺交付时点同样拒绝（维持同路径语义，不收紧其他模式差异） |
| 3 | 宿主二进制缺失时刷新将已有 captured 夹具改写为 unavailable 且 exit 0（capture_host_cli.py:90-99,150-156） | 已有有效捕获（status=captured 且含 help_text）时不写回，明示未更新并非零退出；首次无记录仍记录 unavailable | tmp 合成夹具＋mock which/subprocess：文件逐字节不变、输出明示未更新、退出非零 | 首次 unavailable 记录正例；宿主可用时正常刷新、策展名单保留（既有用例） |
| 4 | 描述续行 `--not-a-real-option means disabled` 被当选项声明（capture_host_cli.py:59-63） | 声明段占位符抹除后含非 flag/逗号/空白文本即判续行跳过；占位符正则容忍 `<FILE>...` 省略号 | 合成帮助文本负例（续行类 flag 不提取） | 既有合成正例（短长别名、仅短选项、占位符内含空格、Examples 排除）全保留；真实夹具只读对照：codex/opencode 零变化、claude 唯一变化为不再提取 `--permission-prompt-tool`（规划阶段已离线预览确认，执行时复核） |

## 3. 执行顺序与门禁

1. 先红：新增/扩展负例测试对当前实现运行，逐项记录失败证据（当前工作树即旧实现）。
2. 实施 4 项修复与 claude.json 白名单处置。
3. 定向回归与静态检查：

```bash
.venv/bin/python -m pytest -q tests/product/skill/test_extensions.py \
  tests/product/cli/test_extension_cli.py \
  tests/product/validation/test_capture_host_cli.py \
  tests/product/validation/test_host_cli_protocol.py \
  tests/product/validation/test_host_acceptance.py
.venv/bin/python -m ruff check src/hetu_stock/skill/extensions.py \
  scripts/host_acceptance.py scripts/capture_host_cli.py \
  tests/product/skill/test_extensions.py \
  tests/product/validation/test_capture_host_cli.py \
  tests/product/validation/test_host_cli_protocol.py \
  tests/product/validation/test_host_acceptance.py
.venv/bin/python -m mypy scripts/capture_host_cli.py scripts/host_acceptance.py \
  src/hetu_stock/skill/extensions.py
git diff --check
```

不默认重跑全量门禁；仅当上述定向结果发现范围外牵连时才报告并停止，不自行扩大。

4. 真实夹具离线前后对照：对四个夹具运行修复后 `_observed_flags`，逐项列出提取差异
   （预期仅 claude 少 `--permission-prompt-tool`），写入交付报告。
5. 检查仅列明文件变化；分两个本地提交：docs(plan) 本阶段文档＋索引、fix 实现＋测试＋
   夹具处置。精确暂存，不 `git add .`；提交前 `git diff --check` 并核对规格/计划相对链接。
6. 本文末追加执行结果补记（红绿证据、测试数、SHA、工作区状态）。

## 4. 完成标准与停止点

- 4 项各自：已证原因、修复位置、行为变化、红绿证据、正向回归齐备；真实夹具离线对照
  逐项说明，白名单依据与未确定部分（`--permission-prompt-tool` 未获证明）如实登记。
- 达到以上标准即停止，交用户复评；不宣称四组全部闭环、认证通过、性能达标或迁移完成。
- 发现范围外问题仅登记不夹带；与计划冲突或必需能力缺失时保留现场、报告最小缺口。
- 回退边界：仅撤销本批可定位改动；既有提交用后续纠正，不整树恢复。

## 5. 执行结果补记（2026-09-25）

- 先红：6 项新增负例对旧实现全部失败——uninstall 越界（absolute/relative 2 例，
  旧实现删除哨兵目录且不报错）、check 缺 delivered_at（probe/非 probe 2 例，旧实现
  passed=True、established）、缺宿主覆盖有效夹具（旧实现改写为 unavailable 且 exit 0）、
  描述续行误取（`--not-a-real-option` 被提取）。
- 后绿：修复后定向回归 **282 passed, 7 skipped**（skip 均为带理由的宿主可用性条件
  skip：zcode 夹具 unavailable、zcode 未安装、已捕获宿主不适用未安装约束等），
  覆盖 `test_extensions.py`、`test_extension_cli.py`、`test_capture_host_cli.py`、
  `test_host_cli_protocol.py`、`test_host_acceptance.py`。
- 静态检查：`ruff check src tests scripts skills/hetu-stock-analysis/scripts` 通过；
  `mypy src` 通过；`check_docs.py` 链接与过期命令检查通过；`git diff --check` 通过。
  未重跑全量门禁（按计划）。
- 真实夹具离线前后对照（修复后解析器对四夹具只读重跑）：codex 提取 31 项、opencode
  25 项，与修复前零差异；claude 提取 76 项，唯一变化为不再提取
  `--permission-prompt-tool`（描述续行泄漏）；zcode unavailable 不参与。三宿主
  `accepted ⊆ observed`、`checked_absent` 互斥断言均通过。
- 白名单处置：`claude.json` `accepted_flags` 移除 `--permission-prompt-tool`
  （1 行删除，其余逐字节不变），登记为「现有证据不足以证明支持」；未加入
  `checked_absent`，不推断宿主不支持，未启动真实宿主补验；其他策展项不变。
- 提交：实现＋测试＋夹具 `8e0177f`；文档与索引见相邻 docs 提交。未推送、未迁移。
- 阻断项状态：4 项均已关闭（负例受控拒绝/保护 + 正向回归成立）；第 4 项遗留
  「`--permission-prompt-tool` 是否受宿主支持」为未确定事项，需未来真实宿主证据，
  本批不补验。
