# HETU 独立数据模块

Python 3.11。发行名 `hetu-data`，导入名 `hetu_data`。基础包使用 pandas、PyArrow 和 DuckDB，可独立于研究包和引擎安装。

## 快速启动

从仓库根目录执行：

```bash
python3.11 -m venv .venv-data
.venv-data/bin/python -m pip install -e './data[web,sources]'
.venv-data/bin/hetu-data web --root .hetu/quant
```

只需 Python／SQL 时可安装基础包 `./data`。基础锁见 [requirements.lock](requirements.lock)，网页、来源及开发依赖的既有锁见 [quant/requirements.lock](../quant/requirements.lock)。从已有 wheelhouse 安装时可用：

```bash
python -m pip install --no-index --find-links /absolute/wheelhouse -c data/requirements.lock hetu-data==0.1.0
```

每个 CLI 入口显式指定数据根。`query`、网页查询和导出均为本地操作。`plan` 预览，`submit` 入队，获准后才由 `worker` 执行。`worker --once` 排空可执行队列后退出。研究侧可消费同一数据根及任务机制。

- [统一使用手册](../docs/quant-research-platform-usage.md)提供配置、命令和 Python／SQL 示例。
- [数据字典与维护合同](../quant/DATA.md)说明字段、单位、读取权限、存储和恢复。
- [初始实现与验收](../specs/2026-11-07-quant-research-platform-initial-implementation.md)区分工程可用、档案覆盖和研究资格。
