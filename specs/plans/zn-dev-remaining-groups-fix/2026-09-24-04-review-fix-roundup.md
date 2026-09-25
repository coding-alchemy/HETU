# 阶段 04：复评七项缺口集中修复

> 文档版本：v1.1
> 文档状态：已批准（2026-09-24 计划批准）并执行完成：7 项缺口修复经两轮复评收口
> （先 `2b84ada`/`3213bab`，第二轮 `2479777`，合并入增量提交 `20fb6ff`）；
> 2026-09-25 复评后修复 host-tools.md 命令名阻断项（`hetu` → `hetu-stock`）
> 创建／修订日期：2026-09-25（v1.1：登记执行完成与复评后修复）
> 范围：复评发现的 7 项 P2 缺口，对应已批准设计 5-A1、5-A2、6-A2、6-A3/A4、6-A4、6-B1
> 及阶段 01「同一只读一致视图」要求的未落实部分；不是新增需求。
> 依据：[路线图](README.md)、[修复设计](../../2026-09-24-zn-dev-remaining-groups-fix-design.md)、
> 用户 2026-09-24 复评结论（7 项，附最小反例与定位）。
> 前序：[阶段 01](2026-09-24-01-metering-integrity.md)、[阶段 02](2026-09-24-02-extension-and-portability.md)、
> [阶段 03](2026-09-24-03-validation-and-handoff.md) 已完成；本阶段不重开其已成立结果。

## 1. 范围与文件

实现：`src/hetu_stock/skill/extensions.py`、`scripts/host_acceptance.py`、
`scripts/capture_host_cli.py`。
测试：`tests/product/skill/test_extensions.py`、`tests/product/cli/test_extension_cli.py`、
`tests/product/validation/test_host_acceptance.py`、`tests/product/validation/test_capture_host_cli.py`。

不改动：既有未提交的 execution-implementation.md 修改（保留、不混入提交）、真实登记表、
真实 zcode db、`.hetu/`、workflow、宿主夹具 JSON、db-delivery、5-B2/B3、6-C、7-B1/B2。
不重跑全量门禁；历史 B1–B4、M2-6、第 7 组认证、第 8 组性能不重开。

## 2. 缺口 → 修复 → 验证映射

| # | 缺口（复评定位） | 违反的已批准要求 | 修复 | 行为验证 |
|---|---|---|---|---|
| 1 | 默认版本选旧版：1.0→1.0.1 后仍选 1.0（extensions.py:543） | 5-A1 自然排序键 | 排序键分两层：`(分段元组, 原字符串)`，原字符串不参与前缀比较 | 1.0→1.0.1 默认选 1.0.1；update 1.0.2 报 previous=1.0.1；01.0/1.0 平局与 release-9/10 用例保持 |
| 2 | 版本记录字段未校验，capabilities=null 致未捕获异常、update 先写后崩（extensions.py:144） | 5-A2「写前拒绝、明确原因、原文件不变」；「下游实际读取的字段形态须满足既有合同」 | `_check_registry_shape` 校验版本记录下游实读字段：capabilities/provides/requires 为字符串列表、files 为 str→str 对象、path/source/summary/compatibility 为字符串（存在即类型正确，缺失沿用缺省语义） | 参数化负例覆盖 list/context/inspect/enable/update：exit 1、无 Traceback、位置精确到字段、登记表字节不变 |
| 3 | status=running、completed_at=NULL、raw 与行一致仍导出成功（host_acceptance.py:2124） | 6-A2「数值一致不单独证明最终结算；不能仅统计 completed」 | 非终态 status（running）抛 `_ZcodeDbExportError`；终态集 completed/error/cancelled，error/cancelled 有可靠 raw 结算仍计入 | running+raw 一致 → exit 1、零输出；error+可靠结算计入的既有正例保持 |
| 4 | 读取未固定同一快照，并发下拼出不存在组合（host_acceptance.py:4454） | 阶段 01「只使用同一只读一致视图完成本轮所需读取」 | db-export 显式只读事务：`isolation_level=None` + `BEGIN`，schema 检查与全部会话收集完成后 ROLLBACK，再发布；不改源库 | 并发注入：首读间另一连接 UPDATE+COMMIT → 修复前混合快照 exit 0（红），修复后写方撞锁 exit 1、零输出 |
| 5 | 同 request 同 attempt 100/150 冲突仍 exit 0 并发布两文件（host_acceptance.py:4517） | 6-A3/A4「先全会话验证，再发布产物」 | `_zcode_db_events` 按 (request_id, attempt) 去重：签名（status/model/四项 token）一致合并为一条、不一致拒绝；在 windowed_by_amid 构建前完成 | 冲突 → exit 1、零输出；相同重复快照 → 单事件 exit 0；两 attempt 分别计量保持 |
| 6 | provenance 发布后清理失败，db-export.json 残留且错误信息失实（host_acceptance.py:4543） | 6-A4「写入错误须诊断并清除本次新建半成品」 | provenance 发布前登记清理清单；FileExistsError 移出清单再抛（不删他人文件）；不新增断电级事务 | 注入「发布后抛错」→ exit 1、两个产物均不存在；既有写失败用例保持 |
| 7 | `--define <KEY --VALUE>` 误取 --VALUE；Examples: 下 --example 缩进行被当声明（capture_host_cli.py:49） | 6-B1「排除描述、示例与参数值中的类 flag 文本」 | 占位符正则允许内含空格（`<[^>]*>`）；Examples/Example 小节门控，非缩进行重置小节 | 合成文本断言 --VALUE/--example/--demo-flag 不进入提取；真实夹具覆盖断言不变（修复前后提取零差异已离线核实） |

## 3. 执行顺序与门禁

1. 先红：新增/扩展测试对当前实现运行，逐项记录失败证据（当前工作树即旧实现）。
2. 实施 7 项修复。
3. 定向回归与静态检查：

```bash
.venv/bin/python -m pytest -q tests/product/skill/test_extensions.py \
  tests/product/cli/test_extension_cli.py \
  tests/product/validation/test_capture_host_cli.py \
  tests/product/validation/test_host_acceptance.py
.venv/bin/python -m ruff check src/hetu_stock/skill/extensions.py \
  scripts/host_acceptance.py scripts/capture_host_cli.py \
  tests/product/skill/test_extensions.py tests/product/cli/test_extension_cli.py \
  tests/product/validation/test_capture_host_cli.py tests/product/validation/test_host_acceptance.py
git diff --check
```

4. 检查仅列明文件变化；分两个本地提交：docs(plan) 本阶段文档＋路线图索引、
   fix 实现＋测试。精确暂存，不 `git add .`，不推送、不合并、不迁移。
5. 本文末追加执行结果补记（红绿证据、测试数、SHA）。

## 4. 完成标准与停止点

7 项各自红→绿成立且直接相关既有用例无回归；ruff 与 `git diff --check` 通过；
提交精确、既有未提交文档原样。完成后停止，交用户复评迁移条件；不宣称四组闭环，
不重判第 03 阶段状态表。

## 5. 执行结果补记（2026-09-24）

红→绿证据：

- 先红（旧实现）：9 项失败均符合预期——`test_default_version_prefers_extended_dotted_suffix`
  断言 `'1.0' == '1.0.1'` 失败（5-A1）；capture 两个用例提取出 `--VALUE`、`--example`、
  `--demo-flag`（6-B1）；`test_db_export_running_status_is_not_settlement_evidence`
  exit 0≠1（6-A2）；`test_db_export_same_attempt_conflicting_usage_refused_before_publish`
  exit 0≠1（6-A3/A4）；`test_db_export_same_attempt_duplicate_snapshot_deduped`
  重复快照未去重；`test_db_export_provenance_publish_failure_is_cleaned_up`
  db-export.json 残留（6-A4）；`test_db_export_reads_one_consistent_snapshot_under_concurrent_writes`
  exit 0≠1（快照）；`test_malformed_version_record_rejects_update_before_any_write`
  未受控 TypeError（5-A2）；参数化字段级畸形 9 用例全部因缺少受控拒绝而失败（5-A2）。
- 修复后：同一批用例全部转绿。

门禁结果：

- 定向回归 4 个测试文件：`261 passed, 1 skipped`（zcode 夹具 unavailable 为既有跳过）。
- `ruff check`（3 实现 + 4 测试文件）：通过；`git diff --check`：通过。
- 真实宿主夹具（claude/codex/opencode captured）在 6-B1 修复后的提取结果与修复前
  零差异，既有能力表未受影响。

提交：

- `2b84ada` docs(plan)：本阶段计划文档与路线图索引。
- `3213bab` fix(phase5)：3 个实现文件 + 4 个测试文件（7 项修复及回归）。
- 执行结果补记提交见本行之后的 git 记录。

未触碰：`specs/2026-09-24-stock-analysis-workflow-v1-phase-5-execution-implementation.md`
的既有未提交修改原样保留在工作区；未重跑全量门禁（1592 passed / 6 skipped 基线继续有效）；
未做任何股票分析。本阶段到此停止，7 项缺口的修复证据提交复评。

## 6. 第二轮复评补记（2026-09-24）

复评结论：6 项关闭，第 5 项（同 attempt 去重与发布前验证）余两处缺口，本轮修复：

- 缺口 1（本轮引入）：去重 `continue` 原位于 raw 结算校验之前，损坏 raw 的重复快照
  是否被拒取决于行序。修复：每条记录先完成结算校验（NULL/解析/逐字段比对），
  再按签名判断是否合并为重复快照。
- 缺口 2（原修复未贯通）：`owner_count` 按原始行数计数，合法重复快照指向同一
  message 时误判「shared by multiple requests」。修复：按不同请求身份
  `(logical_request_id, attempt_index)` 计数；真正多个请求共享 message 的既有
  拒绝用例保持通过。

证据：

- 先红：`test_db_export_duplicate_snapshot_corrupt_raw_refused_in_any_order[False]`
  （先好后坏序退出 0）、`test_db_export_duplicate_snapshot_with_tool_attributes_once`
  （误报 shared by multiple requests）失败，原因与缺口描述一致；
  `[True]` 序在旧实现已拒绝。
- 转绿：定向回归 4 个测试文件 `264 passed, 1 skipped`（新增 3 个用例）；
  `ruff check`、`git diff --check` 通过。

提交：`2479777` fix(phase5)（scripts/host_acceptance.py +
tests/product/validation/test_host_acceptance.py）。本段补记提交见 git 记录。
既有未提交文档修改继续原样保留；未重跑全量门禁；未做任何股票分析。

## 7. 评审后修复补记（2026-09-25）

用户基于增量评审结果要求的收口前修复（不重开功能验收、不重跑历史对账）：

- **阻断项修复**：`skills/hetu-stock-analysis/references/host-tools.md` 第 23、31 行
  `hetu skill extension …` → `hetu-stock skill extension …`（入口名与 `pyproject.toml`
  的 `hetu-stock = "hetu_stock.cli:app"` 及仓内其余文档一致；该文本逐字继承自冻结源，
  属本批增量内容）。修复后扩展机制沿 Skill 文本路径可达。
- **状态同步**：本阶段与路线图头部由「执行中」订正为执行完成（两轮复评收口），
  计划索引补记阶段 04 与本轮修复。
- **非阻断项与冗余登记**：计量工具面 4 项（check 计时 fail-open 死角、db-delivery
  重跑 FileExistsError、NULL `started_at` 静默掉出导出窗口、三个工具归属拒绝分支
  无回归测试）、扩展管理面 4 项（update 同版本重试 FileExistsError、uninstall
  rmtree 取未校验键、validate 畸形登记表报错误导、list 版本字典序展示）、宿主
  CLI/CI 面 3 项（解析器描述续行泄漏与 claude 白名单耦合、宿主二进制缺失静默改写
  白名单为 unavailable、`HETU_HOST_TOKEN` 无消费方）、文档面 1 项（6 个过程提交
  SHA squash 后无分支/标签锚定，是否打标签由用户定夺）及 8 处小型死代码/死数据，
  全部登记至[后半去向 §4](../../2026-09-20-stock-analysis-workflow-v1-phase-5-remainder-requirements.md)，
  不阻碍本次复评收尾，不在本批修复。
- 验证：`check_docs.py` 通过、`git diff --check` 通过；本轮仅 Skill 文本一词修复
  与文档状态同步，未改代码路径，未重跑全量门禁。
