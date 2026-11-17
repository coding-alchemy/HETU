# HETU 量化研究平台使用手册

适用于独立数据模块及量化研究网页。[统一实现与验收](../specs/2026-11-07-quant-research-platform-implementation.md)维护初始能力、工作台、Tushare 已知目录范围内的正式日线库、维护恢复和研究限制。[数据字典](../quant/DATA.md)维护字段、单位、存储和维护合同。

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

本机正式日线根为 `/Users/nan/data/hetu/a-share/daily/`。现行固定库为 `lib-d2ee48254d284873a09d0ac315ec8ccf`，初始化目标选 `daily-initial-20061101-20261013`，首次追补选 `daily-market-increment-20261014-20261116`。既有下载选择仍可固定读取旧库。覆盖和来源限制见[现行交接](../specs/2026-11-07-quant-research-platform-implementation.md#1-现行交付与适用范围)。现有研究根、旧单快照及结果入口见[正式库交付](../specs/2026-11-07-quant-research-platform-implementation.md#34-正式根迁移与资产保护)。

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

在正式根的“本地日线库”选所需的固定库版本，填市场、代码、日期、字段和数值条件，点击“查询日线库”。历史和增量片共用该固定选择。修改表单尚未提交时，当前表格和 CSV 仍对应上次已提交的版本和筛选。导出时重新核对读取权限。用“导出 Notebook 查询选择”下载固定选择，将 `HETU_ARCHIVE_SELECTION` 指向该文件，即可在[档案 Notebook](../quant/notebooks/data_archive.ipynb)重放同一选择。现行库可选择初始化和已存增量两个档案目标；原二十年和扩展历史报告对应历史库。总账区分证据不足、已证应有但缺失和冲突；历史对象全集未证时，全市场覆盖率为空。固定库尚无对应审计时，仍可查询获准原价。详见[现行覆盖](../specs/2026-11-07-quant-research-platform-implementation.md#1-现行交付与适用范围)。

在“数据”页选择档案快照，填写证券代码、日期和所需字段或过滤条件，点击“查询本地数据”。页内可查看原价、量额、来源和空段解释。改筛选后重新查询，CSV 下载使用已提交筛选的结果。出现空段时，根据提示区分停牌、未上市、缺行、字段未知和来源冲突。

页面分别显示固定目标起止、最晚合格原价日和数据根最近成功更新时间。更新时间取自产品处理记录，所选库的实际数据日期看覆盖报告。切换库后重新提交才会更新当前结果及导出。查询中的停牌、来源冲突、范围外和权限限制分别显示。

现行初始化目标按完整有效 Tushare 行情计入原价合格，辅助状态争议单列。展开“来源与查询缺口”可看适用依据及其他来源，未闭合项仍在覆盖页显示。缺成交额保留空值，原件 OHLC 异常保留标记。CSV、CLI 和 SQL 使用同一固定选择的行情字段，状态解释可在网页或 Python `result["gaps"]` 和 `result["status_differences"]` 查看。

用户接受的 7 个代码日在覆盖与查询中单列。查看 Python 查询返回的 `result["accepted_exceptions"]` 可定位决定及原争议。本档案的因子别名读法见[数据字典](../quant/DATA.md#6-工程可用与数据齐备分别检查)。

现行档案按 Tushare 已知目录及已有公开补证确定的对象期间验收。明确的退市日期是退出生效日，当天不再列为应有行情；已经证实的最后交易日仍包含。`002731.SZ / 2026-11-13` 按该边界解释，不填造停牌记录或行情。独立历史全集未经穷尽证明仍是来源限制，不能把已知范围覆盖率称为全市场完整率。

页面“登记来源范围覆盖率”对应上述范围：初始化目标有 7 日已接受例外、无其他未决日；截至 2026-11-16 的追补目标有 133,834 个应有对象日，133,657 日合格原价、177 日已证无交易，无未决日。初始化事实覆盖率仍约 99.999956%，不把接受例外算作实际补齐。

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
3. **执行与追踪**。获得执行授权后，在任务列表复制本次任务号，以 `worker --job-id` 仅执行该维护任务。同根只运行一个 worker。`--once` 处理整个可执行维护队列。例行 M 未启用。
4. **取消或恢复**。在任务列表选取已有任务，查看冻结定义、进度、失败原因、产物、当日用量、整批累计和本次新增。取消先提出请求，由 worker 在请求边界确认。对失败、取消或中断任务显式重开，保留旧记录与累计额度，已成功的分片继续复用。达到日额度后，可在显示的可恢复时间后显式接续，整批批准上限仍有效。传输结果不确定时保留待核。

手动维护以正式数据根中的有效合同为准。选择补齐起点及已结束的目标日，检查实际回看、按日期核定的市场范围、预计用量与待证项目。首次追补的累计额度单独结算，后续手动合同有独立累计额度并共用账户日额。生产备份、独立恢复点及供应商原件例外以[最新恢复交接](../specs/2026-11-07-quant-research-platform-implementation.md#6-t7打包备份与独立恢复)为准。

2026-11-17 至 2026-12-16 的 Tushare 手动合同已完成页面提交和 worker 验证。累计 600 次许可在本次验证后仍剩 600 次。正常维护窗口含两端最多 15 自然日，截面修订按合同保留七自然日回看。提交前核对预览的行情实际日期及日期适用的上市、退出和日历依据。例行 M 关闭。

以下为接口示例，第二、三行只在任务及执行已获准时使用：

```bash
.venv-data/bin/hetu-data plan --root .hetu/quant --request request.json --contracts contracts.json --output plan.json
.venv-data/bin/hetu-data submit --root .hetu/quant --plan plan.json --submission-id my-approved-submission
.venv-data/bin/hetu-data worker --root .hetu/quant --job-id "<已获准的数据维护任务号>"
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
| 来源范围通过，但全市场完整率为空 | 系统按 Tushare 已知目录及公开补证确定的目标验收；独立历史全集证明限制单列。旧库的 `limited` 报告保留原时点含义，不与现行范围混用。 |
