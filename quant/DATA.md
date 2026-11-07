# 本地量化数据使用手册

本手册集中说明**已经实现**的数据字段、存储布局和离线访问方法。来源选型见[数据源评估](../docs/theory/A股量化数据方案评估报告.md)，建设取舍见[数据与数据源方案](../docs/theory/A股量化系统数据与数据源方案.md)，实际覆盖与阶段结论见[初始实现与验收](../specs/2026-11-07-quant-research-platform-initial-implementation.md)。

有数据就可以查询；历史档案可以持续积累。查询入口可用不代表二十年数据已经齐全，也不代表数据已满足历史回测的公开时点要求。

独立安装与任务入口见[数据模块](../data/README.md)。下列公共数据接口来自同一 `hetu_data` 包；旧 `hetu_quant` 兼容入口继续可用。研究资格接口仍归研究侧，数据可查询不替代原 prepare 门禁。

## 1. 日线数据字典

标准表为 `daily_bars`。每行对应一个来源的证券交易日记录；查询保留 `product_id/source_ref`，不会把多个来源同日的记录任意合并。

| 字段 | 含义与单位 | 范围查询返回类型／规则 |
| --- | --- | --- |
| `code` | 带市场后缀的原代码，如 `600519.SH`；沪／深／北后缀为 SH／SZ／BJ | 字符串；不去前导零，不擅自跨码拼接 |
| `date` | 行情所属日期 | `YYYY-MM-DD` 字符串；查询起止两端均包含 |
| `open_raw/high_raw/low_raw/close_raw` | 未复权开／高／低／收盘价，元／股 | 浮点数；不以除权参考价替代原价 |
| `volume_shares` | 该日累计成交量，**股** | 浮点数；来源以手计量时在标准化层转换 |
| `amount_cny` | 该日累计成交额，**人民币元** | 浮点数；不等于收盘价乘成交量 |
| `close_price_return` | 已有价格连续口径辅助列 | 可能缺失；存在值不自动证明复权窗口、锚点合格 |
| `close_total_return` | 已有总收益连续口径辅助列 | 可能缺失；不能用原价冒充包含分红的总收益 |
| `product_id` | 产品标识，例如 `ts_a_daily` | 字符串；用于来源及读取权限核验 |
| `source_ref` | 原件／导入来源引用 | 字符串；结合同一快照清单定位，不伪改成当前下载地址 |

网页和默认 Notebook 只输出原价 OHLC、日成交量、日成交额及来源。没有默认连续复权展示，成交量和金额也不做复权变换。这里是日线接口，未包含分钟线、逐笔成交或账户交易记录。

旧导入文件可能存有数字字符串，新提供方可能存为数值；文件保持原样，`read_range` 与 SQL 日线视图统一转为浮点数。缺失或无法解析的日线数值返回空值，页面列为 `fields_missing`，不填零、不补前值。CSV 没有类型信息，再读时应显式保留代码和日期为字符串。

## 2. 辅助表与时间含义

下列是日线使用及研究准备主要涉及的字段；所有表均保留 `product_id/source_ref`。**表已定义不表示目标股票与期间的数据已经取得或验证。**

| 表 | 主要字段 | 作用与限制 |
| --- | --- | --- |
| `securities` | `code/name/market/ipo_date/out_date/security_type/status` | 证券目录和生命周期观察；不能据当前目录替代完整历史股票池 |
| `calendar` | `date/is_trading_day` | 包含非交易日记录；记录缺失不等于休市 |
| `security_status` | `code/date/is_st/is_suspended/is_listed` | 停牌、风险警示和上市状态；未知不冒充正常 |
| `adjustment_factors` | `code/date/adj_factor` | 无量纲复权辅助；同日冲突及窗口缺口需单独核验 |
| `corporate_actions` | `code/ex_date/action_type/m/d/terms_raw/available_at/record_date/pay_date/stock_listing_date` | 实施日、登记日、现金到账日、股票可卖日各自保存；`m` 为股数乘数、`d` 为每股现金金额；查询成功空仅证明实际查询口径 |
| `action_versions` | `code/revision_id/div_proc/ann_date/published_at/supersedes/ex_date/record_date/pay_date/stock_listing_date/observed_at` | 区分预案、实施及修订，不以公告日冒充实施日 |
| `market_cap` | `code/date/market_cap_cny/available_at` | 市值以元计；日期化市值不等于当时已公开可用 |
| `security_observations` | `code/date/name/total_shares/observed_at/revision_id` | 股本以股计；供应商观察不自动升级为历史公开版本 |
| `historical_securities` | `code/valid_from/valid_to/listed/market/board/available_at/evidence_ref` | 历史身份、有效期间和证据；未知继承关系不跨码接续 |
| `knowledge_evidence` | `table_name/record_key/revision_id/supersedes/published_at/evidence_ref/sha256/verified` | 原件、修订及核验状态；部分来源附加 `observed_at`，不能替代 `published_at` |
| `price_limits` | `code/date/reference_price/up_limit/down_limit` | 元／股；除权参考与原始行情分开 |
| `security_events` | `code/date/event_type/suspend_timing` | 状态事件及时间说明 |
| `terminal_disposals` | `code/date/cash_per_share/available_at/evidence_ref/sha256` | 有依据的退出处置，每股现金以元计；不能凭退市日期虚构补偿 |
| `trading_rules` | `rule_id/market/board/security_type/valid_from/valid_to/min_buy_qty/qty_step/tick_size/settlement/limit_rule/fee_rule` | 历史交易制度；数量以股计，价格步长以元计 |
| `index_bars` | `code/date/close/amount_cny/return_basis` | 指数点位、金额及价格／收益口径 |
| `boards` | `board_id/board_kind/name/provider/classification_version` | 行业／主题目录和分类依据 |
| `theme_library` | `theme_code/theme_name/parent_code/parent_name` | 题材发布者名称与层级 |
| `memberships` | `board_id/code/observed_at/available_at/effective_from/effective_to/membership_kind` | 成员关系；当前观察不等于历史成员 |
| `financial_reports` | `code/report_period/ann_date/available_at`，另附来源提供的财务字段 | 报告期、公告及可得时间分开；财务指标是否可用由来源及用途资格决定 |

完整登记列和单表标准化键位于 [TABLE_SCHEMAS](../data/src/hetu_data/store.py)。它约束标准化基础结构；不能替代跨来源、跨版本的唯一性或 PIT 判断。时间字段区分：事实所属／生效日期、当时公开时间、本次取得或观察时间。历史版本选择继续走研究准备接口，本地行情查询不重新评价这些资格。

## 3. 存储布局与格式

默认数据根为 `.hetu/quant/`；`HETU_QUANT_ROOT` 可指定另一个数据根。每个根有自己的产品配置、读取授权和快照，查询不会隐式合并其他批次根。

```text
<data-root>/
  data-products.json          # 可选的根目录配置；否则用 hetu_data 包资源
  data-entitlements.json      # 本机授权范围，不含请求 token；缺失不默认有付费权限
  evidence/                  # 公开时点等原件，按 evidence_ref 引用
  snapshots/<snapshot_id>/
    manifest.json            # 产品、来源、请求范围、文件哈希、覆盖及修订关系
    raw/<product>/...        # 提供方原始响应或原始导入文件
    tables/<table>/*.parquet # 标准化表
  results/                   # 已发布研究结果，与数据档案分开
```

- **原始层**：保持供应商原始响应／导入原件；格式取决于来源，可能为 JSON、CSV、Parquet 等。不能只备份标准表而丢弃原件。
- **标准层**：Parquet。新 `ts_a_daily` 采用产品＋年度分区、ZSTD 压缩、按代码／日期排序；旧代码＋月分区和导入分区继续读取，不为格式统一重写旧数据。其他产品沿用各自布局，不能声称全库都已年度化或同一压缩格式。
- **快照清单**：列出实际消费的文件和哈希。年度文件中的逐代码覆盖另记在 `object_coverage`；旧分区可在文件条目保留 `coverage`。文件存在或请求成功不等于所有交易日齐备。
- **发布与版本**：临时目录完成后发布；修订产生新快照，旧引用和结果保留。`as_of` 变化不自动使旧结果失效。
- **访问引擎**：DuckDB 直接读 Parquet；没有再复制一份全量行情到持久数据库。范围查询先裁剪已知月／年／代码分区，再由 DuckDB 过滤日期、代码并投影所需列。

普通查询核验**实际选择的标准文件**哈希，不扫描所有无关资产和原件；它不是全库完整性审计。完整样本备份核验清单、raw、标准表及引用的 evidence。数据不足的片段仍能被查询，返回的缺口解释与全档案审计分开保存。

## 4. Python、SQL、网页与 CSV

在仓库根目录、`quant/.venv` 环境运行。以下 `snapshot_id` 换成所选根内的已发布快照；不使用“自动最新快照”来改变历史查询。

```python
from datetime import date
from pathlib import Path
from hetu_data import DataStore
from hetu_data.archive import query_archive, open_archive_view
from hetu_data import SnapshotRef

root = Path(".hetu/quant").resolve()
store = DataStore(root)
ref = SnapshotRef(snapshot_id="请填写快照ID", root=root)
selection = dict(codes=("600519.SH",), start=date(2026, 1, 1), end=date(2026, 10, 13))

# 只需少数字段时；代码参数化，表名与列名必须登记。
close = store.read_range(ref, "daily_bars", **selection,
                        columns=("code", "date", "close_raw", "source_ref"))

# 与网页相同：原价OHLCV/额/来源，加本次空段解释。
result = query_archive(store, ref, **selection)
bars, gaps = result["bars"], result["gaps"]

with open_archive_view(store, ref) as con:
    sql_rows = con.execute(
        "SELECT code,date,close_raw,volume_shares,amount_cny,source_ref "
        "FROM daily_bars WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
        ["600519.SH", "2026-01-01", "2026-10-13"],
    ).df()
```

`read_range` 返回 pandas DataFrame，列顺序与传入 `columns` 一致；空结果保留列。日期筛选针对 `date` 或公司行动的 `ex_date`。无这两列的表仅做代码／列筛选，**不提供历史有效区间或 PIT 版本选择**；需要这些语义时使用现有研究准备接口。`read_table` 保持旧行为，适用于确实需要整表的调用。

`open_archive_view` 返回需要关闭的 DuckDB 连接，`daily_bars` 是指向该快照的只读视图，不允许直接更新源表；连接本身不是任意 SQL 的安全沙箱。打开全视图须有其中所有数据的读取权限；只有子范围权限时使用 `read_range`，不能借 SQL 绕过。

网页：按 [统一使用手册 §3](../docs/quant-research-platform-usage.md#3-启动网页) 启动，进入“数据”→选档案快照→填写代码和日期→“查询本地数据”。页面显示原价 K 线、量额、来源、空段解释；CSV 下载与当前已提交筛选一致。改筛选后需重新点击查询，下载不会启动 worker 或获取数据。

Notebook：[data_archive.ipynb](notebooks/data_archive.ipynb)，包含同一快照的 Python、SQL、CSV 实例及逐列一致性检查。可直接修改首个代码格，也可配置 `HETU_QUANT_ROOT`、`HETU_ARCHIVE_SNAPSHOT`、`HETU_ARCHIVE_QUERY`。从其他工作目录启动时请填绝对数据根路径。

`gaps` 列为 `code/start/end/reason`，解释包括 `not_listed`（未上市）、`after_exit`（退出后）、`suspended`（已证停牌）、`missing_bar`（交易日无行情）、`calendar_unknown`、`lifecycle_unknown`、`fields_missing`、`conflicting_bars`。不把这些情况统称“零成交”；存在多来源同日记录时保留明细、暂不画歧义 K 线。

免费和付费是来源属性，存在本地文件不会自动获得许可。范围查询、SQL、网页和备份均核验读取授权；到期后能否保留使用取决于已登记许可，不自动延长期限。查询失败只展示失败原因，补数据必须单独发起更新任务。

## 5. 小样本备份与恢复

先选定已获准复制的快照和**尚不存在**的目标目录。以下入口用于单个有界样本，会核对容量并复制 manifest、登记的 raw／标准文件、引用的 evidence、产品配置及既有授权记录；不复制 token、任务数据库或整个工作区。来源字段和原快照 ID 保持不变。

```python
from hetu_data.archive import backup_archive, verify_archive_backup

saved = backup_archive(store, ref, Path("/绝对路径/新的样本备份目录"))
restored_ref = verify_archive_backup(saved.root)  # 缺文件会指出具体路径
restored_store = DataStore(saved.root)
same_bars = restored_store.read_range(restored_ref, "daily_bars", **selection,
                                    columns=("code", "date", "close_raw", "source_ref"))
```

完整备份会读取所选样本的表来核验授权和原件引用，不能当成已验证的全量流式备份器。校验通过证明这份备份可重开，**同盘副本不提供设备故障保护**。备份不会放宽许可，恢复后仍按当前授权读取；运行失败留下的目录应先检查原因，不覆盖已有内容重试。

## 6. 工程可用与数据齐备分别检查

工程入口可用的证据是：已有数据可筛选读取、来源可查、多入口数值一致、空结果有解释、样本备份可恢复、查询零外部请求。全历史齐备还需要按目标对象、生命周期、交易日和产品逐段核对，另由档案／研究资格流程证明。

数据采集按获批范围及接口额度长期补缺，不因查询入口上线自动重抓旧数据。维护任务的已实现操作和适用限制见下一节；维护工程与全历史覆盖分别判定。


## 7. 日更与15自然日恢复

“数据”页的“日更与断更恢复”读取本机`<data-root>/maintenance-contracts.json`，先**预览**再提交。默认free；选择paid不代表已经获得获取、导入或读取许可。预览只读登记资产，页面不调用提供方、不启动worker。日更／恢复的`start`和目标日独立输入，不能改变研究截止或档案初始化T。

合同沿用`plan_update`的`products/limits`，每个产品需明确`max_days/upstream_per_fetch/sdk_calls/time_basis/table/revision_lookback_days`。`time_basis`只接受`trading_days`或`natural_days`；历史修订不能恢复时用`unavailable_revisions`登记具体版本／期间与原因，`historical_revisions=false`不承诺完整PIT。没有可靠扣额单位或无法预留的字节额度时，不能自行造预算，执行会在发送前受限。

`maintenance`需列：

- `start`：独立维护窗口起点；15自然日例为目标日减14天，包含两端，不是15交易日。来源完整修订回看可早于该起点，由`query_start`及各域`start/refresh`展示，执行请求必须覆盖它；授权不足明确受限，不截短。
- `history_ref`：已有独立目标CSV，至少含`code/market/security_type/valid_from/valid_to/lifecycle_status/source_ref`。未知身份保留待核，不能拿当前存续目录代替历史集合。
- `calendar_markets`：已核日历适用市场，不默认所有市场等价；日历未知不猜休市。可选`coverage_snapshot_ids`限定本根的已登记资产，空列表不代表完整；不隐式扫描其他数据根。
- `asset_snapshots`：明确登记的共享快照引用，每项含`root/snapshot_id/tables`，可选`config_path`；也兼容合同顶层同名引用。仅在对应owner的读取许可／对象／期间内消费已存标准表，不搬库、不隐式发现其他根。
- `target_scope`：目标来源及完整性说明；维护成功不会把它升级为全历史池已齐。

日历回看必须形成独立有界查询，`refresh_from`等于该查询起点；支持时实际取回新值并记录同源有界修订关系，保留旧快照。其他目录直接刷新、非有界日历刷新仍明确拒绝，不能当缓存复用成功。

合约及目标可缺一域而不阻断其他已证域。覆盖按实际行／有效字段与查询证据分开核对；成功空不是未上市、停牌或无事件。自然日发布／修订保留恢复窗口内曾存续的退出对象，不漏周末；来源不能提供的旧版本继续列不可恢复。

`limits`必须引用本次具体批准范围`approved_scope_ref`，以及实际获批的fetch、上游、尝试、重试等计数。不以凭据、账户积分、本地文件或0元新增费用替代授权；也不把示例数字写成供应商日限。可执行维护受逐请求计量证据和批准范围限制。已有 Tushare 及 BaoStock 的指定产品／操作有界计量证据，不外推到其他产品或未证适配，不自动切换收费源。已有免费资产和本地查询不受此执行限制替代。

预览显示各域`last_success/target/covered/missing/refresh/unrecoverable`、未知／失败／成功空、模式、预计计量及额度。`last_success`是本维护窗口中的已证数据截止，不是采集时间；空值不表示全库从未成功。提交会冻结具体计划、真实模式和目标事实，进入既有JobService队列；人工启动worker后才执行。当前任务运行、停止和重开在“任务列表”查看，页面刷新／重开只读取，不增加任务。

取消运行中任务先显示`cancel_requested`，等待worker在请求边界确认；不是按下按钮就已停止。重试创建新任务记录，但复用同一批准额度账本，已完成段不重复；多上游fetch取消后，已成功原件验哈希并保留原取得时点，只发送剩余上游。前次传输结果不确定则待核，不盲发。相同批准引用下改变额度会被拒绝，新的额度需新的明确批准，不能用新点击／新定义／新日期重置。

报告位置为`updates/<definition_id>/jobs/<job_id>/update.json`，直播计量为同目录`progress.json`。同一批准范围账本保存在`updates/approved-usage/<安全路径名>/usage.jsonl`；安全路径名仅用于存储额度记录，不判断数据／旧结果等价或过期。

- `actual_usage`：累计账本用量，重开保留。
- `incremental_usage`：本次调用新增，重复执行已完成计划应为0。
- `maintenance.domains`：执行后只读复核的内容覆盖和限制；任务`succeeded`仅说明请求完成，不能替代内容／PIT／研究资格。
- `snapshot_ids/pending/failures`：已发布片段和未完成段。失败、取消或额度耗尽不删除旧快照，已有合法读取权限的本地数据仍可查询。

依然不配置常驻worker、计划任务或后台采集。取得许可到期后禁止新请求；只有登记允许留存读取的资产才能继续使用，不推定供应商永久保留权。维护软件机制已验证，未来执行仍须具备授权及预算。三期已按修订来源范围收口，档案 limited、原研究资格 blocked。
