# HETU 量化研究平台使用手册

适用于独立数据模块及量化研究网页。[初始实现与验收](../specs/2026-11-07-quant-research-platform-initial-implementation.md)说明初始能力和限制。[系统完善实施记录](../specs/2026-11-07-quant-system-improvement-implementation.md)记录已验收的 01–03 工程及仍为 `limited` 的 04 档案。[数据字典](../quant/DATA.md)维护字段、单位、存储和维护合同。

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

本机正式日线根为 `/Users/nan/data/hetu/a-share/daily/`。查询第三步交付数据时，选择固定库 `lib-4940ba04874044eeb61ddcc7893b33de`，既有库仍可选择。覆盖、读取期限和来源限制见[第三步实施记录](../specs/2026-11-07-quant-system-improvement-implementation.md#83-2026-11-13-第三步集中收尾)。现有研究根、旧单快照及结果入口见[正式库交付](../specs/2026-11-07-quant-system-improvement-implementation.md#6-第一步正式日线库交付)。

## 3. 启动网页

```bash
# 独立数据页
.venv-data/bin/hetu-data web --root "$HOME/data/hetu/a-share/daily"

# HETU 的“数据”页用正式根，研究和旧结果仍用原根
HETU_DATA_ROOT="$HOME/data/hetu/a-share/daily" \
  quant/.venv/bin/python -m streamlit run quant/src/hetu_quant/ui/app.py
```

独立数据页用来查询档案和管理数据任务。研究页另有“任务列表”“数据”“研究提交”“策略目录”“回测”“结果与导出”。打开、刷新、查看任务和查询已发布数据不会启动 worker 或触发外部取数。

## 4. 查询、筛选和导出

在正式根的“本地日线库”选所需的固定库版本，填市场、代码、日期、字段和数值条件，点击“查询日线库”。历史和增量片共用该固定选择。修改表单尚未提交时，当前表格和 CSV 仍对应上次已提交的版本和筛选。导出时重新核对读取权限。用“导出 Notebook 查询选择”下载固定选择，将 `HETU_ARCHIVE_SELECTION` 指向该文件，即可在[档案 Notebook](../quant/notebooks/data_archive.ipynb)重放同一选择。选择“固定档案目标”可分别查看二十年子集、更早历史和已存增量的覆盖与限制。总账区分证据不足、已证应有但缺失和冲突；完整历史全集未证时，全市场覆盖率为空。固定库尚无对应审计时，仍可查询获准原价。详见[正式库覆盖总账](../specs/2026-11-07-quant-system-improvement-implementation.md#7-正式库覆盖总账)。

在“数据”页选择档案快照，填写证券代码、日期和所需字段或过滤条件，点击“查询本地数据”。页内可查看原价、量额、来源和空段解释。改筛选后重新查询，CSV 下载使用已提交筛选的结果。出现空段时，根据提示区分停牌、未上市、缺行、字段未知和来源冲突。

页面分别显示固定目标起止、最晚合格原价日和数据根最近成功更新时间。更新时间取自产品处理记录，所选库的实际数据日期看覆盖报告。切换库后重新提交才会更新当前结果及导出。查询中的停牌、来源冲突、范围外和权限限制分别显示。

CLI 可复用网页导出的选择。将下载的文件保存为 `selection.json`，从仓库根执行：

```bash
.venv-data/bin/python - <<'PY'
import json
from pathlib import Path
from hetu_data.cli import main

s = json.loads(Path("selection.json").read_text())
raise SystemExit(main([
    "query", "--root", s["root"], "--library", s["library_id"],
    "--codes", ",".join(s["codes"]), "--markets", ",".join(s["markets"]),
    "--start", s["start"], "--end", s["end"], "--columns", ",".join(s["columns"]),
    "--predicates", json.dumps(s["predicates"]), "--output", "bars.csv",
]))
PY
```

## 5. Python 与 SQL

```python
import json
from datetime import date
from pathlib import Path
from hetu_data import DataStore, LibraryRef
from hetu_data.library import query_library, query_library_sql

s = json.loads(Path("selection.json").read_text())
store = DataStore(Path(s["root"]))
ref = LibraryRef(s["library_id"], store.root)
result = query_library(
    store, ref, codes=tuple(s["codes"]), markets=tuple(s["markets"]),
    start=date.fromisoformat(s["start"]), end=date.fromisoformat(s["end"]),
    columns=tuple(s["columns"]), predicates=tuple(s["predicates"]),
)
bars, gaps = result["bars"], result["gaps"]
sql_bars = query_library_sql(store, result["selection"], "SELECT * FROM daily_bars")
```

在 [档案 Notebook](../quant/notebooks/data_archive.ipynb) 的 Python 内核中设置 `HETU_ARCHIVE_SELECTION` 为该 JSON 的绝对路径。旧单快照入口仍可用，使用 `--snapshot` 或 `SnapshotRef`；`open_archive_view` 需要对该快照全视图有读取许可。字段和单位见[数据手册](../quant/DATA.md)。

## 6. 预览、提交与管理数据任务

1. **预览**。数据页选择拉取或日更与断更恢复。日常维护选择目标日期、产品、来源模式、接续库版本，以及按代码维护产品的市场和证券代码，可填写拟申请的整批提供方、上游及网络尝试次数。预览会显示缺口、预计调用和费用。填写的次数要与相应批准额度一致才能提交。首次配置的维护合同在数据根的 `maintenance-contracts.json`，字段见[数据手册 §7](../quant/DATA.md#7-日更与15自然日恢复)。CLI 的 `plan` 只写预览文件，输入由 `data_request_to_dict` 序列化的请求及产品接口合同。
2. **提交**。只有范围和额度获准后才冻结计划。网页或 CLI 会将它放进持久队列，返回定义号和任务号。同一提交标识幂等，新提交使用新标识并沿用对应的批准额度账本。
3. **执行与追踪**。获得执行授权后才启动对应 worker。独立数据 worker 处理数据维护，研究 worker 还处理研究任务。同根只运行一个 worker。`--once` 排空可执行的队列后退出。当前采集仍暂停，例行 M 未启用。
4. **取消或恢复**。在任务列表选取已有任务，查看冻结定义、进度、失败原因、产物、当日用量、整批累计和本次新增。取消先提出请求，由 worker 在请求边界确认。对失败、取消或中断任务显式重开，保留旧记录与累计额度，已成功的分片继续复用。达到日额度后，可在显示的可恢复时间后显式接续，整批批准上限仍有效。传输结果不确定时保留待核。

正式数据根尚未配置例行维护合同。获准后按数据根、产品和期间登记合同及额度，再预览和提交。历史获取额度已结案。获准标准表及恢复元数据的生产备份和独立恢复已通过。供应商响应原件列为例外，后续备份仍需对应操作许可。

以下为接口示例，第二、三行只在任务及执行已获准时使用：

```bash
.venv-data/bin/hetu-data plan --root .hetu/quant --request request.json --contracts contracts.json --output plan.json
.venv-data/bin/hetu-data submit --root .hetu/quant --plan plan.json --submission-id my-approved-submission
.venv-data/bin/hetu-data worker --root .hetu/quant --once
```

研究队列入口为 `quant/.venv/bin/python -m hetu_quant.worker --root .hetu/quant --once`，默认不加 `--once` 时常驻轮询。两种队列均需先确认其中等待执行的任务。

T7 备份采用手动预览、打包、校验和独立恢复。已发布获准标准表及恢复元数据的生产恢复点。命令和接续方式见 [T7 手动打包备份](../quant/DATA.md#51-t7-手动打包备份)。

## 7. 查看研究产物

- **策略目录**。按市场、假设、频率和研究状态查找策略，查看规则、来源、适配、全部试验及其关联评价。执行、研究进度、研究结论和经济状态各看自身证据。可以登记现成清单和附件，导入只保存证据。已适配的策略可以从目录进入回测配置。配置使用表单，已保存定义可以复制并查看成本、参数的具体差异。超出冻结试验的修改可以保存为草稿，正式提交仍需原预算及资格核对。
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
