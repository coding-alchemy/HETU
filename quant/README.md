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

配置、任务预览／执行／恢复及结果使用见[统一使用手册](../docs/quant-research-platform-usage.md)。字段和存储见[数据字典](DATA.md)，验收见[初始实现](../specs/2026-11-07-quant-research-platform-initial-implementation.md)。仅需数据管理时可使用[独立数据模块](../data/README.md)。

## 开发检查

从仓库根目录运行离线检查。回测相关用例需既有隔离引擎环境：

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests -q
quant/.venv/bin/python -m ruff check quant
```
