# HETU 量化研究子项目（一期）

独立 Python 3.11 环境，不进入根环境；现行行为、验收及能力限制见[一期实现与验收](../specs/2026-10-17-quant-research-platform-phase-1-implementation.md)，本文件只写安装与使用。

## 安装

在仓库根目录执行：

```bash
python3.11 -m venv quant/.venv
quant/.venv/bin/python -m pip install -e 'quant[dev]'
quant/.venv/bin/python -m pip check
```

需要本机装有 Python 3.11（`requires-python = ==3.11.*`）。完整解析结果保存在 `quant/requirements.lock`。

运行资产默认写入仓库下 `.hetu/quant/`（已被 `.gitignore` 忽略）；网页与测试可用环境变量 `HETU_QUANT_ROOT` 把根目录指向其他位置（如临时目录）。

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
