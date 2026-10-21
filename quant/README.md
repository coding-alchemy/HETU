# HETU 量化研究子项目

独立 Python 3.11 环境，不进入根环境；现行行为、验收及能力限制见[一期实现与验收](../specs/2026-10-17-quant-research-platform-phase-1-implementation.md)及[新二期软件实现与限定验收](../specs/2026-10-21-quant-research-platform-phase-2-implementation.md)，本文件只写安装与使用。

## 安装

在仓库根目录执行：

```bash
python3.11 -m venv quant/.venv
quant/.venv/bin/python -m pip install -e 'quant[dev]'
quant/.venv/bin/python -m pip check
```

需要本机装有 Python 3.11（`requires-python = ==3.11.*`）。完整解析结果保存在 `quant/requirements.lock`。

运行资产默认写入仓库下 `.hetu/quant/`（已被 `.gitignore` 忽略）；网页与测试可用环境变量 `HETU_QUANT_ROOT` 把根目录指向其他位置（如临时目录）。

正式引擎使用独立隔离环境 `.hetu/quant/runtime/rqalpha/.venv`，不进入主环境。`quant/probes/probe_engine.py` 是离线探查，满足条件时执行合成引擎并写证据，不是只读命令。RQAlpha 6.4.1 的实际用途许可已由用户决定，原判断依据保留于 `.hetu/quant/qualification/phase2/engine-license-review.json`；准入为 candidate_ready（semantics.costs=full），见 `engine-admission-20261017e/`。

运行锁 `.hetu/quant/runtime/rqalpha/requirements.lock` 的原字节源码副本为 [quant/engine/requirements.lock](engine/requirements.lock)，用于恢复相同依赖，不含许可凭据；RQAlpha 6.4.1／NumPy 1.26.4／PyArrow 17.0.0 与主环境分离。运行锁和原探查消费路径保留；主环境仍用 `quant/requirements.lock`。本次只保全锁和执行两环境 `pip check`，不安装或升级依赖。

引擎入口为 `quant/engine/runner.py`，使用本地 bundle、制度解析与次日开盘撮合。已证范围包括 T+1、开盘涨跌停保守拒绝、价格步长、申报数量、容量和历史显式费用；滑点进入成交价，现金不重复扣滑点，gross_nav 仅加回显式费用。分红／送转按登记、除息、实际到账及可卖日期消费，差别化股息税、退出受阻和未知处置保留轨迹。真实 RQAlpha 合成账本见 `test_backtest_account.py` 及上述软件实现记录；这不表示支持所有行动类型或真实来源资格齐备。

## 测试

在仓库根目录运行：

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests -q
quant/.venv/bin/python -m ruff check quant
```

测试全部离线：数据用 `quant/tests/fixtures.py` 的合成材料，不访问网络、不产生收费调用。

## 运行

worker（任务执行，常驻轮询）：

```bash
quant/.venv/bin/python -m hetu_quant.worker --root .hetu/quant
```

worker 一次排空队列后退出（适合脚本化与自验）：

```bash
quant/.venv/bin/python -m hetu_quant.worker --root .hetu/quant --once
```

网页（默认读写 `.hetu/quant`，可用 `HETU_QUANT_ROOT` 覆盖）：

```bash
quant/.venv/bin/python -m streamlit run quant/src/hetu_quant/ui/app.py
```

### 中断与恢复语义

- worker 持有 `.hetu/quant/worker.lock` 本机排他锁：第二个 worker 进程启动即失败退出，不会并行执行同一队列。
- worker 正常取得锁后，会把上次遗留的 `running`／`cancel_requested` 任务标为 `interrupted`（保留原原因与产物位置），不自动重排；旧 worker 仍持锁存活时不会误标。
- `interrupted`／`failed`／`cancelled` 任务可经网页或 `JobService.retry` 显式重试：生成新任务号、新提交标识，attempt 加一，原记录保留。
- 同一 `submission_id` 重复提交返回原任务、不新增任务；终态任务不会被再次领取计算。
- 快照与结果发布后即锁定（哈希校验），刷新／重开网页、重启 worker 都读取同一批已发布产物，不重新计算。

独立策略研究脚本见 `quant/research/`，例如：

```bash
quant/.venv/bin/python quant/research/low_volatility.py \
  --data-root .hetu/quant --snapshot-id <快照ID> \
  --as-of "<信息截止时间>" --signal-date <月末交易日> \
  --out .hetu/quant/research/<实验名>
```

Notebook 示例：`quant/notebooks/read_result.ipynb`（读取同一结果产物，不重复计算公式）。

## 回测页面与导出（正式入口与合成验证）

Streamlit 侧边栏“回测”页提供：已登记策略（`low_volatility`／`universe_vw`；
E/P 等未接入候选只显示规则与缺口）、区间、初始资本、基准与成本配置；
“检查资格与限制”预览与 worker 共用 `prepare_backtest`（不降低门槛）；
“提交任务”经 JobService（同次重复提交幂等，终态后的新点击产生新任务，
刷新/重开不重跑）。任务由上述 worker 执行：正式任务经 `run_formal_job`
复用 `run_validation.py` 的冻结、预算、阶段、共同信号及生产引擎；机制任务
使用相应执行入口。结果发布在 `.hetu/quant/backtests/<run_id>/`。

结果区先显示研究/经济结论与限制，再展示净值、成本、持仓/现金、成交、
信号等九张固定表；“导出 ZIP（只读已发布产物）”经
`hetu_quant.backtest_reporting.export_backtest` 导出，与发布值一致、
不重新计算。两个已发布回测可用 `compare_backtests` 并列比较：股票池/
区间/执行口径/基准不同则不混排（`mixed_ranking_allowed=False`），
同池的成本等变更可并列比较并逐项标明。

正式网页→worker→共同信号→真实 RQAlpha→账户／评价→导出及 Notebook
已有合成完整链路证据（`.hetu/quant/acceptance/phase2/dp-integration-20261018/`）。
完整真实数据资格仍未通过，真实研究尚未执行；合成成功不代表经济目标达成。


2026-10-18 二期正式入口：回测页以完整冻结母协议选择八次具体试验/阶段，实际解析预览和worker共用调度/资格；页面提交不能绕过预算、冻结或阶段依赖。低波动正式研究仍月频/36月/市值权重，未接入策略仅显示缺口。网页、导出ZIP与Notebook读取同一已发布BacktestRef，Notebook可用HETU_QUANT_BACKTEST_RUN_ID指定引用，不触发重算。完整合成真实引擎集成在test_phase2_flow.py；真实数据资格仍受阻，未执行真实研究。

导入新证据可在import manifest声明base_snapshot_ids，生产先核免费准入和原字节哈希，按父快照顺序整表继承；本次导入表替换同名继承表，保留旧快照及实际查询覆盖，不自动升级历史口径。

周/月机制验证仅提供透明universe_vw市值基准，warmup0，日历证明周期末后由共同信号和真实引擎运行；经济not_evaluated。低波动正式研究仍为月频，机制入口不再空信号运行。
