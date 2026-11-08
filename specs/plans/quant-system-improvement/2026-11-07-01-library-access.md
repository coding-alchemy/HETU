# 量化系统完善 01：固定库引用与统一离线读取执行计划

> **面向执行 Agent：** 实施时使用 `subagent-driven-development` 或 `executing-plans`，逐任务完成复验；执行方式不替代人工批准。
> 文档版本：v1.0
> 文档状态：已完成，工程范围通过；固定库及单快照查询复评关闭
> 创建日期：2026-11-07；修订日期：2026-11-08
> 适用范围：D4、D3版本与来源接缝、D6读取边界；支撑S1／S2／S5

**目标：** 用户用一次固定选择读取已登记的历史与增量分片，查询、SQL和导出同口径，新发布不改变已经提交的选择。

**架构：** 在现有 `DataStore`、`SnapshotRef`、Parquet与DuckDB上增加轻量库发布清单。清单固定资产、文件及版本选择，不复制行情；独立数据包保留单快照入口，网页和维护阶段消费同一合同。

**技术栈：** 既有Python、pandas、DuckDB、Parquet、pytest；不新增数据库、调度器或通用API框架。

**规格：** [v0.3需求§3–5](../../2026-11-07-quant-system-improvement-requirements.md)、[v0.3设计§2–4／6](../../2026-11-07-quant-system-improvement-design.md)。

## 1. 全局约束与前置条件

共同授权、阶段顺序、测试门禁和保护范围见[路线图](README.md)及[项目约束](../../../AGENTS.md)。计划获批后才修改实现；留在 `zn_quant`，不建分支／worktree，不提交。

- 仅消费明确登记且当前可读的资产；零外部请求，免费模式无收费回退，凭据不代表读取授权。
- 保留旧快照和文件原字节，库清单只组织引用；哈希只检测锁定资产变化，不判断新旧或使旧验收过期。
- 库引用可读不表示目标覆盖完整，更不表示研究资格通过；旧 `limited`／`blocked` 和研究预算 `0/8` 不改判。
- 本阶段不建设备份或覆盖审计；02消费本阶段清单，03消费本阶段选择与查询。仅增量验证本次接缝，不整批重跑既验项目。
- 测试材料先遵循[材料方法论](../../../docs/engineering/test-fixtures-methodology.md)，复用原始事实构造器；预期值仅留测试侧。

## 2. 评审重点（Review Focus）

| 场景与预期 | 所属新增行为测试 |
| --- | --- |
| 最新增量只有一天，旧历史仍通过同一库引用可读 | `test_publish_accumulates_registered_shards` |
| 未登记邻根／文件存在，查询不得自动加入 | `test_publish_accumulates_registered_shards` |
| 同源无证修订与跨源异值都不能按mtime、hash或as_of择值消失 | `test_library_revision_and_source_conflicts` |
| 查询后发布新版本，SQL／导出仍使用原版本与已提交筛选 | `test_library_queries_pin_selection_and_permissions` |
| 查询后读取许可缩小，后续SQL／导出必须拒绝越权，合法单快照仍兼容 | `test_library_queries_pin_selection_and_permissions`、`test_library_cli_and_standalone_readers` |

## 3. 既有基线与本次文件边界

既有 `SnapshotRef(snapshot_id: str, root: Path)` 不改名、不改变语义。`DataStore.read_range(self, ref: SnapshotRef, name: str, *, codes: tuple[str, ...], start: date, end: date, columns: tuple[str, ...], product_id: str | None = None) -> pd.DataFrame` 已按登记分区裁剪，核对当前产品／行范围权限。

既有 `query_archive(store, ref, *, codes, start, end)` 返回 `snapshot_id/mode/bars/gaps`；`filter_bars(bars: pd.DataFrame, *, columns: tuple[str, ...], predicates: tuple[dict, ...]) -> pd.DataFrame` 保留来源和冲突。`open_archive_view(store: DataStore, ref: SnapshotRef) -> duckdb.DuckDBPyConnection` 继续用于旧单快照，不扩展为库长期授权凭证。

新增生产文件仅 `data/src/hetu_data/library.py`；修改 `models.py`、`__init__.py`、`archive.py`、`cli.py` 及访问文档。新增测试 `data/tests/test_library.py`，独立安装增量场景写入 `data/tests/test_standalone_flow.py`；不复制测试事实库。

## 4. 任务一：登记并发布不可变库引用

**文件：** 新增 `library.py`／`test_library.py`；修改 `models.py`／`__init__.py`。以下路径均相对仓库根。

**消费：** 既有 `DataStore.load_manifest(ref: SnapshotRef) -> dict[str, object]`、`check_access(mode, product, *, operation="read")`、`worker_lock`；登记字典沿用 `archive.scan_local_coverage(..., asset_snapshots=())` 的 `root/snapshot_id/tables/raw_products?/config_path?`。

**产出（新增）：**

```python
@dataclass(frozen=True)
class LibraryRef:
    library_id: str
    root: Path

def publish_library(store: DataStore, *, asset_snapshots: tuple[Mapping[str, object], ...],
                    previous: LibraryRef | None = None) -> LibraryRef: ...
def load_library(store: DataStore, ref: LibraryRef) -> dict: ...
def list_libraries(store: DataStore) -> list[dict]: ...
```

清单写入 `<root>/libraries/<library_id>/manifest.json`，临时目录完成后原子发布。`previous` 必须属于该库根；新清单继承旧登记和版本选择再并入本次显式登记，不把最新一片当全库。发布失败不修改旧清单，不创建行情副本。

`load_library` 返回固定字段：`library_id/root/previous_library_id/created_at/status/asset_snapshots/table_files/revision_selections/source_policy/sources`。`status` 为 `readable` 或 `limited`，仅表达发布引用及冲突状态，不使用 `complete`。

- `asset_snapshots` 保存明确登记的完整字典；`raw_products` 仅供回源、02审计与备份，不自动把原件当标准表查询。
- 每条 `table_files` 固定 `root/snapshot_id/manifest_sha256/config_path/table/product_id/path/sha256/partition`；原有 `coverage`、`object_coverage`、`mode` 与原件关联从固定源清单回查。`config_path` 可为 `None`。
- `sources` 为 `root/snapshot_id/product_id/mode/manifest_sha256` 去重列表；行中的 `source_ref` 连同根、快照定位原件，不改写成新下载地址。
- `revision_selections` 每项明确 `root/product_id/table/superseded_snapshot_id/selected_snapshot_id/codes/start/end/evidence_ref`。来源登记可附同名列表；仅接受可回源并匹配实际同源修订范围的supersedes／版本依据，不能只凭 `revises` 字符串宣布整片替换。
- 同一实际文件重复登记去重；同源同键同事实重复不增加记录，所有原件引用仍可回查。无证异值全部保留且标 `limited`；已证修订只在登记键范围选择新版，旧库引用不变。跨源值全部保留，不能用主源选择掩盖差异。
- `source_policy` 登记 `primary_product_id` 和 `basis_ref`。若用于原价档案覆盖裁定，依据须记录 `purpose=daily_archive`、主源、对照产品、代码、起止日、适用字段和具体口径；发布锁定其字节，越界或变动继续保留冲突。公共查询保留两源原值。未证主源填 `None` 并标受限，不通过文件时间猜测。

`list_libraries` 只列当前根明确发布的清单摘要，返回 `library_id/previous_library_id/created_at/status/source_policy/sources`；不扫邻根、不解析“自动最新”。02使用完整清单恢复登记根及原件关联，并保持相同版本选择规则。

- [x] **1.1 写反例。** 在 `test_library.py` 用 `source_facts.response(day=..., price=...)` 经真实导入形成历史、增量、重复和冲突片；只改变触发字段，来源差异保留原值。测试名为评审重点前3行。

```python
assert history_query["bars"].date.tolist() == ["2026-01-05", "2026-01-06"]
assert unregistered_root not in {s["root"] for s in manifest["sources"]}
assert old_manifest_bytes == old_manifest_path.read_bytes()
assert conflict["status"] == "limited" and set(conflict["bars"].close_raw) == {10, 11}
```

- [x] **1.2 运行红灯。** `quant/.venv/bin/python -m pytest -c quant/pyproject.toml data/tests/test_library.py -k 'publish_accumulates or revision_and_source' -q`；先补可导入的最小签名，再确认历史片遗漏／无证冲突被覆盖等核心断言失败，不能把导入或环境错误当红灯。
- [x] **1.3 实现登记、发布与版本选择。** 只检查被明确登记的源清单、文件边界与当前读取权；用已有写锁保护原子发布，不建立另一队列。附带修订证据不成立时保留冲突并明确受限，哈希／创建时刻仅用于溯源和锁定。
- [x] **1.4 运行同组测试。** 必须通过，并断言只新增清单元数据、无全量行情副本；修改mtime或增加无关新快照不能改变固定引用。
- [x] **1.5 记录任务结果。** 产出清单例子和反例结果；建议提交主题 `feat: publish fixed library references`，仅另获提交授权后执行。

## 5. 任务二：按固定引用提供范围、缺口和SQL读取

**文件：** 修改 `library.py`、`archive.py`、`test_library.py`。消费任务一的 `LibraryRef/load_library`；复用现有范围读取、字段转换、条件筛选和缺口解释，必要时提取私有函数，不复制另一套规则。

**产出（新增）：**

```python
def read_library_range(store: DataStore, ref: LibraryRef, name: str, *, codes: tuple[str, ...],
                       start: date, end: date, columns: tuple[str, ...],
                       product_id: str | None = None) -> pd.DataFrame: ...
def query_library(store: DataStore, ref: LibraryRef, *, codes: tuple[str, ...],
                  start: date, end: date, markets: tuple[str, ...] = (),
                  columns: tuple[str, ...] = ARCHIVE_COLUMNS,
                  predicates: tuple[dict, ...] = ()) -> dict: ...
def query_library_sql(store: DataStore, selection: Mapping[str, object], sql: str,
                      parameters: tuple[object, ...] = ()) -> pd.DataFrame: ...
```

`read_library_range` 的日期两端包含、空结果列序、数值转换及 `product_id` 规则沿用 `read_range`；允许额外投影 `source_root/snapshot_id`。只读取清单固定文件并应用 `revision_selections`，不重新扫描当前快照目录。每次实际读取按资产所属根核查当前产品、对象、市场和期间许可，再核验所选文件哈希；返回列不能绕过内部核权列。

`query_library` 返回 `{selection, status, bars, gaps, sources}`。`selection` 是深复制的JSON字典，字段为 `library_id/root/codes/markets/start/end/columns/predicates`，日期为ISO字符串，代码／字段／条件为数组；`root` 是绝对库根。市场使用SH／SZ／BJ筛选原代码段，先取代码与市场交集再读取和解释缺口，不跨代码拼接；空市场数组不加市场限制，代码仍须明确选择。

`bars` 为pandas表，必保留 `code/date/product_id/source_ref/source_root/snapshot_id` 和所选字段；`gaps` 沿用 `code/start/end/reason`，按完整已选库范围解释后再做数值筛选，不把筛掉的行误报数据缺失。`status/sources` 沿用固定清单，查询范围内冲突明示；许可拒绝抛 `AccessDenied`，不能返回伪空成功。

SQL合同仅为所选 `daily_bars` 的单条只读查询：每次按 `selection` 重建同一已授权选择，将本次结果注册为内存表再执行并返回DataFrame；关闭该连接的外部读取及扩展加载，拒绝写入／多语句和查询外部路径，不能绕过已选范围或触网。不暴露长期连接，只约束本入口，不扩展为通用SQL沙箱。导出由调用方用同一选择重放 `query_library` 后序列化CSV，不新建导出引擎。03保存已提交选择，未提交编辑不能替换它。

- [x] **2.1 写冻结与核权反例。** `test_library_queries_pin_selection_and_permissions`：查询后发布另一库版本／修改表单输入，重放旧selection仍相等；缩小读取授权后重放和SQL均失败；SQL请求未登记文件或写入时拒绝且无外部调用。冻结参数先还原日期／tuple再传函数。

```python
pd.testing.assert_frame_equal(before["bars"], replay["bars"])
pd.testing.assert_frame_equal(replay["bars"], sql_rows)
assert replay["selection"] == submitted_selection
with pytest.raises(AccessDenied):
    query_library_sql(store, submitted_selection, "SELECT * FROM daily_bars")
```

- [x] **2.2 运行红灯。** `quant/.venv/bin/python -m pytest -c quant/pyproject.toml data/tests/test_library.py -k queries_pin -q`，应在新行为断言失败。
- [x] **2.3 实现读取。** 保持单快照公开签名与返回不变；读取函数不得调用 `prepare`／Provider／网络，缺少PIT或复权辅助不阻止合法原价返回；不前填行情或补造停牌bar。
- [x] **2.4 运行同组测试。** 同时断言空筛选保留列、跨源冲突保留、无关分区损坏不影响本次读取；对SQL传入未登记文件读取及写入语句应拒绝且无外部请求。只核所选资产，不借查询执行全库审计。
- [x] **2.5 记录任务结果。** 交付02／03可直接消费的调用例子；建议提交主题 `feat: query pinned library selections`，提交仍另行授权。

## 6. 任务三：独立入口与兼容交付

**文件：** 修改 `data/src/hetu_data/cli.py`、`data/tests/test_standalone_flow.py`、`quant/DATA.md`、`data/README.md`。新增行为测试 `test_library_cli_and_standalone_readers`；网页与Notebook实际接入由03完成。

**消费／产出：** CLI保留原 `query --root --snapshot --codes --start --end --columns --output`；新增 `--library` 与 `--snapshot` 二选一。库查询补 `--markets` 和 `--predicates`（沿用数值条件JSON格式），不改旧单快照默认行为；新增 `libraries --root` 输出任务一摘要，不隐式选择某个版本。

- [x] **3.1 写独立包反例。** 复用 `isolated_data_env/source_facts` 和现有独立流程的网络／研究包禁用方式，只新造本阶段必要的两片事实；通过库CLI、Python、一次性SQL读取同一选择并逐字段比较CSV。
- [x] **3.2 运行红灯。** `quant/.venv/bin/python -m pytest -c quant/pyproject.toml data/tests/test_standalone_flow.py::test_library_cli_and_standalone_readers -q`；应因新CLI或库行为尚缺而失败。
- [x] **3.3 接入CLI并更新手册。** 明示库根、发布版本、来源、缺口和当前读取权限；文档区分库工程可读与完整覆盖，不要求用户手工拼批次，不把技术版本选择隐藏成自动最新。
- [x] **3.4 运行同组测试。** 断言CLI／Python／SQL逐字段一致、原单SnapshotRef仍可读、`hetu_quant/research/rqalpha` 不可导入且外部请求为0。旧独立安装证据有效，不因新日期或源码版本补跑整个旧套件。
- [x] **3.5 记录交付。** 建议提交主题 `feat: expose library queries in standalone data tools`；另获授权才commit，提交前按仓库约定检查空白及文档相对链接。

## 7. 阶段完成、停止与回退边界

三个新增行为测试组通过即证明本阶段工程接缝；必要回归仅覆盖被修改的旧 `query_archive/filter_bars` 和CLI单快照分支。可复用的既有测试定位为 `test_filter_export_matches_python_and_sql`、`test_cli_plan_and_query_are_offline`、`test_kernel_runs_without_research_imports`，仅在对应语义受影响时选择重验并记录依据。

本阶段不执行真实股票分析、不重新获取历史、不消耗正式研究预算。源权限／原件／版本依据不足时报告具体资产及可读范围，保持旧快照与旧库清单可用；不能为“通过”静默跨根、替换来源或宣布全库完整。02接收固定清单做覆盖与恢复，03接收固定selection完成网页流程；库发布工程通过不代替S2完整历史验收。
