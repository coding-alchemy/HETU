# HETU 量化研究平台使用手册

适用于独立数据模块及量化研究网页。[初始实现与验收](../specs/2026-11-07-quant-research-platform-initial-implementation.md)说明实际能力和限制。[数据字典](../quant/DATA.md)维护字段、单位、存储和维护合同。

## 1. 安装与环境

在仓库根目录使用 Python 3.11。仅查询、导出或管理数据任务时可独立安装 `data/`：

```bash
python3.11 -m venv .venv-data
.venv-data/bin/python -m pip install -e './data[web,sources]'
```

只需 Python／SQL 读取时可安装基础包 `./data`，网页与来源依赖可按需添加。研究网页在独立环境中先装数据包，再装研究包：

```bash
python3.11 -m venv quant/.venv
quant/.venv/bin/python -m pip install -e './data[web,sources]'
quant/.venv/bin/python -m pip install -e './quant[dev]'
```

已安装的环境可直接使用。数据基础依赖见 [data/requirements.lock](../data/requirements.lock)，研究与可选组件见 [quant/requirements.lock](../quant/requirements.lock)。需要受控回测时，RQAlpha 使用独立的 `.hetu/quant/runtime/rqalpha/.venv` 与[引擎锁](../quant/engine/requirements.lock)，不装入研究主环境。引擎探查会执行合成回测并写证据，普通查询无需执行。

## 2. 配置数据根与来源

默认研究根为仓库下的 `.hetu/quant`，研究网页和 Notebook 可用 `HETU_QUANT_ROOT` 指定其他根。独立数据 CLI 每次传 `--root`。记录要读取的快照 ID。相同代码出现在多个批次根时，按各自登记的资产和读取权限选择，入口不会自动发现或合并其他根。

产品配置按显式路径、根内 `data-products.json`、包内配置依次解析。付费获取、导入和读取还受根内 `data-entitlements.json` 中各操作授权约束。需要执行已获准的 Tushare 任务时，可在执行进程环境中设置 `TUSHARE_TOKEN`，或在源码仓库的私有本地 `.hetu/credentials/tushare.env` 配置同名变量。文件须为非符号链接，组和其他用户没有读写权限。凭据不放入计划、命令行参数、产物或 Git。仅本地查询无需配置请求凭据，已有数据仍须有读取权。

现行来源边界为 Tushare 与公开信息，免费模式无收费回退。原资产保留已登记来源与权限，Q1 已取消。接口权限、账本额度和本次执行批准分别检查。

## 3. 启动网页

```bash
# 独立数据页
.venv-data/bin/hetu-data web --root .hetu/quant

# 研究页，默认根为 .hetu/quant
quant/.venv/bin/python -m streamlit run quant/src/hetu_quant/ui/app.py
```

独立数据页用来查询档案和管理数据任务。研究页另有“任务列表”“数据”“研究提交”“回测”“结果与导出”。打开、刷新、查看任务和查询已发布数据不会启动 worker 或触发外部取数。

## 4. 查询、筛选和导出

在“数据”页选择档案快照，填写证券代码、日期和所需字段或过滤条件，点击“查询本地数据”。页内可查看原价、量额、来源和空段解释。改筛选后重新查询，CSV 下载使用已提交筛选的结果。出现空段时，根据提示区分停牌、未上市、缺行、字段未知和来源冲突。

CLI 同样按快照读本地行。例如，把 `SNAPSHOT_ID` 和日期换成已知有权读取的范围：

```bash
.venv-data/bin/hetu-data query --root .hetu/quant --snapshot SNAPSHOT_ID \
  --codes 600519.SH --start 2026-01-05 --end 2026-01-05 \
  --columns code,date,close_raw,source_ref --output bars.csv
```

## 5. Python 与 SQL

```python
from datetime import date
from pathlib import Path

from hetu_data import DataStore, SnapshotRef
from hetu_data.archive import open_archive_view

store = DataStore(Path(".hetu/quant"))
ref = SnapshotRef("SNAPSHOT_ID", store.root)
bars = store.read_range(
    ref, "daily_bars", codes=("600519.SH",),
    start=date(2026, 1, 5), end=date(2026, 1, 5),
    columns=("code", "date", "close_raw", "source_ref"),
)

with open_archive_view(store, ref) as con:
    rows = con.execute(
        "SELECT code, date, close_raw, source_ref FROM daily_bars "
        "WHERE code = ? AND date BETWEEN ? AND ? ORDER BY date",
        ["600519.SH", "2026-01-05", "2026-01-05"],
    ).fetchall()
```

`open_archive_view` 需要对该快照全视图有读取许可。仅授权部分行时用 `read_range`。SQL 视图指向同一 Parquet 快照，连接需要关闭。可复用[档案 Notebook](../quant/notebooks/data_archive.ipynb)及[数据手册 §4–5](../quant/DATA.md)中的调用和样本备份说明。

## 6. 预览、提交与管理数据任务

1. **预览**。数据页选择拉取或日更与断更恢复，先查看产品、对象、期间、覆盖缺口、预计调用和权限限制。维护合同在数据根的 `maintenance-contracts.json`，字段见[数据手册 §7](../quant/DATA.md#7-日更与15自然日恢复)。CLI 的 `plan` 只写预览文件，输入由 `data_request_to_dict` 序列化的请求及产品接口合同。
2. **提交**。只有范围和额度获准后才冻结计划。网页或 CLI 会将它放进持久队列，返回定义号和任务号。同一提交标识幂等，新提交使用新标识并沿用对应的批准额度账本。
3. **执行与追踪**。获得执行授权后才启动对应 worker。独立数据 worker 处理数据维护，研究 worker 还处理研究任务。同根只运行一个 worker。`--once` 排空可执行的队列后退出。当前采集仍暂停，例行 M 未启用。
4. **取消或恢复**。在任务列表查看进度、失败原因、产物位置和累计／本次计量。取消先提出请求，由 worker 在请求边界确认。对失败、取消或中断任务显式重开，保留旧记录与累计额度，已经确认完成的分片不重复。传输结果不确定时保留待核，避免盲发。

以下为接口示例，第二、三行只在任务及执行已获准时使用：

```bash
.venv-data/bin/hetu-data plan --root .hetu/quant --request request.json --contracts contracts.json --output plan.json
.venv-data/bin/hetu-data submit --root .hetu/quant --plan plan.json --submission-id my-approved-submission
.venv-data/bin/hetu-data worker --root .hetu/quant --once
```

研究队列入口为 `quant/.venv/bin/python -m hetu_quant.worker --root .hetu/quant --once`，默认不加 `--once` 时常驻轮询。两种队列均需先确认其中等待执行的任务。

## 7. 查看研究产物

- **榜单**。在“结果与导出”选择已有发布结果，查看行业、主题、重复关系、风险及 Gate，下载 ZIP。真实已验收榜单的截止日为 2026-10-13，原结果继续对该时点有效。
- **回测**。在“回测”页选择已登记策略及冻结定义，先点“检查资格与限制”。预览和 worker 使用相同资格规则。已有发布结果可查看九张表、研究／经济结论及限制，也可只读导出 ZIP。比较结果需核对池、区间、基准和执行口径，明确成本等实质差异。
- **Notebook**。[结果 Notebook](../quant/notebooks/read_result.ipynb)读取同一数据根的发布产物。可用 `HETU_QUANT_BACKTEST_RUN_ID` 指定已发布回测，读取和导出都不会重算信号、排名或引擎结果。

查看已发布结果、资格预览和提交新的研究任务是不同操作。原正式研究目前受阻，真实预算保持 0/8。受控数据的真实引擎验证已经完成，实际策略经济效果仍未评价。

## 8. 常见限制

| 现象 | 如何解释 |
| --- | --- |
| 能查日线，资格仍受限 | 档案查询核验的是读取权和实际行情。正式研究还需历史股票池、公开时点版本、行动及退出等用途资格。 |
| 任务成功，某些交易日仍没有行 | 成功空响应证明该次查询。需结合日历、生命周期及状态判断缺行。 |
| 能读某些日期，整库 SQL 视图被拒绝 | 用范围接口读取被授权的代码和期间。现行许可不自动扩展到整个快照、备份或订阅到期后的使用。 |
| 看不到某个快照 | 确认实际数据根、快照 ID 和当前许可。原件和数据不会随代码检出。 |
| 档案可用但覆盖为 `limited` | 二十年目标窗口固定。历史全集、特殊标识及未证差异仍限制整体覆盖，记录见[初始实现](../specs/2026-11-07-quant-research-platform-initial-implementation.md)。 |
