# HETU 量化研究子项目

个人 A 股研究、排名、回测软件和结果查看。采用独立的 Python 3.11 环境，与仓库根的个股分析环境分开。

## 安装

从仓库根目录执行。已有环境可复用：

```bash
python3.11 -m venv quant/.venv
quant/.venv/bin/python -m pip install -e './data[web,sources]'
quant/.venv/bin/python -m pip install -e './quant[dev]'
```

主环境精确依赖见 [requirements.lock](requirements.lock)。正式回测使用独立 RQAlpha 运行时和[引擎锁](engine/requirements.lock)。`probes/probe_engine.py` 是会写证据的合成引擎探查，日常只读使用无需执行。

## 运行

```bash
quant/.venv/bin/python -m streamlit run quant/src/hetu_quant/ui/app.py
```

默认数据根为 `.hetu/quant`，用 `HETU_QUANT_ROOT` 指向其他根时，worker 的 `--root` 也须相同。网页刷新、查询和导出不取数。独立研究脚本位于 `research/`， [Notebook](notebooks/read_result.ipynb) 可读已发布排名和回测。真实研究资格仍为 `blocked`，预算 0/8。

本工作区已归档的因子库自测收据位于 `.hetu/quant/factor-evidence/factor-selftests/`。从仓库根启动时可显式选用这份归档：

```bash
PYTHONPATH="$PWD/quant/src:$PWD/data/src" \
HETU_FACTOR_EVIDENCE_ROOT="$PWD/.hetu/quant/factor-evidence" \
  "${hetu_quant_python:-quant/.venv/bin/python}" -m streamlit run quant/src/hetu_quant/ui/app.py
```

`HETU_FACTOR_EVIDENCE_ROOT` 指向包含 `factor-selftests/` 的目录。复用其他量化虚拟环境时，可将 `hetu_quant_python` 设为该环境的 Python 路径。工作台在「因子库」按因子、版本和参数读取已保存的执行收据，并显示实际读取的目录。

在「因子库」搜索因子，打开「用教学样例试算」可带入观察日、对象及默认参数。调整表单参数后点「计算所选输入」，完成后在读数或缺口下打开相关证据。也可在「数据 → 因子输入」选择固定发布查询，再把一条记录带回试算。需要读取其它数据根时，启动前设定 `HETU_DATA_ROOT`。本地发布须有 `factor_calculation` 用途许可才能用于试算。

因子试算、数据许可和验收边界见[因子试验工作台实现](../specs/2026-11-26-quant-factor-workbench-implementation.md)。

配置、任务预览／执行／恢复及结果使用见[统一使用手册](../docs/quant-research-platform-usage.md)。字段和存储见[数据字典](DATA.md)，验收见[统一实现与验收](../specs/2026-11-07-quant-research-platform-implementation.md)。仅需数据管理时可使用[独立数据模块](../data/README.md)。

## 开发检查

从仓库根目录运行离线检查。回测相关用例需既有隔离引擎环境：

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests -q
quant/.venv/bin/python -m ruff check quant
```
