# 量化系统完善阶段02：原价覆盖、连续维护与恢复

> 文档版本：v1.0
> 文档状态：已完成，工程范围通过；原价审计、维护及备份恢复机制继续供 04 使用
> 创建日期：2026-11-07；修订日期：2026-11-08
> 适用范围：D1–D3、D5–D6及S2／S3的软件接缝；真实建库执行归阶段04
> 执行提示：获批后使用 `subagent-driven-development` 或 `executing-plans` 逐任务实施；执行方式服从用户选择。

**目标：** 固定独立历史目标，正确区分原价覆盖、辅助用途与研究资格；同一批准计划按日额度接续，获准库备份能恢复。
**架构：** 扩展现有 `hetu_data.archive/updates/worker/access`，消费阶段01固定的 `LibraryRef`；保留旧单快照入口和原执行链。
**技术栈：** Python、pandas、Parquet、DuckDB、SQLite、pytest；不新增存储或调度框架。
**依据：** [需求v0.3](../../2026-11-07-quant-system-improvement-requirements.md) §3／§3.1／S2–S5；[设计v0.3](../../2026-11-07-quant-system-improvement-design.md) §3／§6；[路线图](README.md)。

## 1. 全局约束与前置条件

- [路线图](README.md)的共同硬约束全部适用；本阶段计划获批后才改实现。当前分支工作，不创建分支或worktree；提交仍需另行明确授权，main提交前再次确认。
- 依赖阶段01的 `LibraryRef`、固定清单和版本选择合同；只读登记资产，不扫描其他根，不重写旧库或使旧结果过期。
- 初始目标固定为2006-10-13至2026-10-13；新增维护目标另行固定，不随系统日期滚动删除早期历史。
- 本阶段仅用临时根与合成传输验证机制，外部请求、真实股票分析和正式策略运行均为0；生产目标／能力核验、用量批准和取数归阶段04。
- 测试材料修改前读取[维护方法论](../../../docs/engineering/test-fixtures-methodology.md)；复用现有case，仅补必要事实差异，断言不进入供应商响应或输入材料。
- 保留原331日差异、既有辅助和未知记录；工程通过不等于S2真实全库完整，原研究仍须独立资格门。

## 2. 评审焦点与测试归属

| 焦点 | 直接行为与负责测试 |
| --- | --- |
| 独立目标遗漏退市／改码对象，或未知日历被当休市 | 任务1 `test_raw_archive_independent_segments_and_market_calendar`；缺行情不缩分母，未知留缺口 |
| 跨分片重复、缺amount、缺PIT／因子混用完整结论 | 任务1 `test_raw_archive_counts_and_auxiliary_are_separate`；对象交易日只计一次，辅助不足单列 |
| 成功空、截断及正成交／停牌冲突被当已闭合 | 任务1 `test_raw_archive_empty_and_trade_conflict_remain_open`；任务2复用已有截断原件测试 |
| 跨天／换任务重置批次额度或重发已成功上游 | 任务2 `test_daily_quota_resume_preserves_batch_usage`、`test_daily_quota_inside_fetch_resumes_remaining_calls` |
| 有读取权即复制，或只复制行情丢失库清单 | 任务3 `test_library_backup_permissions_and_manifest_restore`、`test_library_restore_rejects_missing_registered_asset` |

## 3. 任务1：独立目标与原价完整性审计

**文件：** 修改 `data/src/hetu_data/archive.py`、`data/src/hetu_data/updates.py` 的规划费用传递、必要的 `data/src/hetu_data/store.py` 日历列保留、`quant/DATA.md`；测试 `quant/tests/test_data_archive.py`。`quant/src/hetu_quant/data_archive.py`仅按需兼容导出，不实现第二套审计。

**已有合同与复用：** `archive_requirements(history, calendar, cutoff, *, years=20, start=None)`、`scan_local_coverage(store, ref, *, asset_snapshots=(), progress=None)`、`audit_archive(store, ref, requirements, *, inventory=None)`、`plan_coverage_gaps(audits, store, *, as_of, contracts, domain_products)`。
原价必需字段固定为 `open_raw/high_raw/low_raw/close_raw/volume_shares/amount_cny`；单位为元／股、股、人民币元，来源及版本来自登记文件和快照。
扫描已有日期位图、实际行校验和无交易事件，不能把 `stats.tables` 的物理行数当覆盖分子。`audit_research_coverage`继续维护研究PIT和行动门，不读取新的原价完成标记放行。

**输入与输出接口：**

- 扩展 `archive_requirements(history: pd.DataFrame, calendar: pd.DataFrame, cutoff: date, *, years: int = 20, start: date | None = None, calendar_scopes: tuple[Mapping[str, object], ...] = ()) -> dict`。
- 新目标的history列为 `identity_id/code_period_id/code/market/security_type/valid_from/valid_to/lifecycle_status/source_ref`；一个证券实体的不同代码段、市场期间分别成行，不能覆盖旧段。
- `history.attrs['target_scope']`固定为 `{complete: bool, source_ref: str, source_version: str, unknown_ranges: list}`；该来源与版本说明独立目标，不用下载代码或返回行生成全集。旧单快照调用兼容原输入，新原价完整判定要求来源与范围可证。
- `calendar_scopes`每项含 `product_id/market/valid_from/valid_to/source_ref`；calendar保留 `product_id/date/is_trading_day/source_ref`。同一日历适用于多个市场须分别有依据，不默认沪深北等价。
- requirements保留 `objects/start/end/target_scope/unknown_ranges/exclusions`，增加 `expected_sessions_by_market`及 `calendar_unknown_ranges`；旧 `expected_sessions`只供原兼容入口。
- 新增 `audit_library(store: DataStore, ref: LibraryRef, requirements: dict) -> dict`；内部 `_scan_library_coverage(store: DataStore, ref: LibraryRef) -> CoverageInventory`复用既有逐文件扫描及日期位图。
- 消费01的 `load_library(store, ref)` 中 `asset_snapshots/table_files/revision_selections/source_policy`，先按固定文件与已证修订选择扫描，再按对象／市场期间审计；不能重新选“最新”或仅拼接所有旧版本。
- 报告保留 `coverage/gaps/unknown_ranges/units/source_refs/query_observations/read_failures`，增加 `target_start/target_end/latest_available_date/raw_price_complete/adjustment_complete/target_scope_complete`。
- `coverage_counts={expected, qualified_bars, no_trade, unknown_or_conflict, outside_scope}`：按已知目标及可证交易日计数，`qualified_bars + no_trade + unknown_or_conflict == expected`且三类互斥；no_trade含已证全天停牌，缺行／字段未知归第三类。未能枚举的对象／日历缺口单列，不伪造分母；`outside_scope`另计可证范围外已存对象日。按 `(identity_id, code_period_id, market, date)` 去重。
- `coverage_ratio`仅在目标全集及日历分母可证时给出；否则为 `None`，`known_scope_ratio`只描述已证范围。`latest_available_date`为最晚合格原价日，不宣称此前连续完整。
- `raw_price_complete`要求独立目标、适用日历、必需字段、缺行解释及原价冲突全部闭合；`adjustment_complete`独立。辅助缺失／冲突／无读取许可只限制对应辅助，不通过无关read_failures阻塞原价。旧 `audit_archive`的既有返回合同保持，不据新分层追溯改判历史结果。
- 新增 `list_archive_targets(store: DataStore) -> list[dict]`，只列 `<root>/archive/targets/<target_id>.json` 中 `{target_id, requirements}` 的 `target_id/start/end/target_scope` 摘要；阶段04冻结该目标，日常页面不要求用户填文件路径。
- 新增 `load_archive_coverage(store: DataStore, ref: LibraryRef, target_id: str) -> dict | None`，只读 `<root>/archive/coverage/<library_id>/<target_id>.json` 的已存审计报告，无报告返回None。04或明确审计动作原子写报告；页面刷新不执行全库扫描。
- 目标及报告使用上述固定JSON字段，无额外目录扫描或新账本；target_id／library_id仅用于定位，新目标或新库不会作废旧报告。当前引用没有报告须显示未审计，不能借旧报告宣布当前范围完整。

- [x] **步骤1：补三组行为测试。** 复用 `make_backtest_case('coverage')`、`build_case_store`，目标已有SH普通股、SZ退市股、BJ新股；测试侧建立阶段01发布引用。
  - `test_raw_archive_independent_segments_and_market_calendar`：删除退市对象的行情仍保留目标分母及缺口；已证不重叠旧码／新码段分别统计，同码不同实体未证期间仍unknown；BJ上市前不计入BJ目标。
  - 同测试分别注入一个市场日历unknown、另一市场同日开市，断言unknown不转False、其他市场日期不被污染，`raw_price_complete is False`、`coverage_ratio is None`。
  - `test_raw_archive_counts_and_auxiliary_are_separate`：基准8个对象日，重复登记同片及增加同值来源后 `expected == qualified_bars == 8`；删除因子及PIT不改原价通过，`adjustment_complete is False`，研究门保持原结论。
  - 同测试只删一个amount，断言 `qualified_bars == 7`、`unknown_or_conflict == 1`、`raw_price_complete is False`；全集未证时即使8行齐全也只能给known_scope_ratio。
  - `test_raw_archive_empty_and_trade_conflict_remain_open`：空响应无无交易证据时缺口保留；缺bar但独立全天无交易事实可计no_trade；正成交与停牌冲突仅计unknown_or_conflict，不重复计入合格行／无交易。
  - `test_coverage_plan_keeps_mode_and_unknown_cost`：免费请求遇付费domain产品必须拒绝，不暗改mode；未证费用返回None，合同明确无新增费时才为0。将输出requests继续传入生产 `plan_update(...,audited_segments=...)` 后断言同样成立，不能在第二次规划时重置成0；审计unknown仍随计划返回，已知可执行段不冒充全目标ready。
  - 同原价计数测试保存固定目标／报告，断言list/load按明确引用返回且不调用scanner；新库无报告返回None，旧报告仍可读取。

- [x] **步骤2：先使最小接口可导入，再运行新增测试，确认因分母／缺口／费用等目标行为失败，不把导入失败当业务红灯。**

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests/test_data_archive.py -q -k 'raw_archive or coverage_plan_keeps'
```

- [x] **步骤3：实现上述合同。** 市场日历保留三值及适用期；有证代码期间允许分别核验，重叠／未知映射不拼接。仅解释缺bar的停牌事实不要求ST、财务或完整研究状态齐全。
  - 原价行校验和事实冲突共用既有规则，合格原价仍要求全部量额字段；主源选择仅接受01固定 `source_policy` 的证据，保留其他来源原值与差异，不按mtime裁决331日差异。
  - 扩展 `plan_coverage_gaps(audits, store, *, as_of, contracts, domain_products, mode: str = 'free') -> dict`：沿传显式mode，免费遇付费产品拒绝；继续只合并真实缺行，unknown与来源结构性缺口单列。
  - 输出保留 `requests/counts/unknown_ranges/unresolved/executed=False`；`new_data_fee_cny`及 `counts.data_fee_cny` 未证为None，只有合同明确本次无新增费才为0，不能将规划未执行误报免费。`plan_update`沿用同一费用依据规则，覆盖审计和正式任务预览不得采用不同默认值；不新增通用计费引擎。known可执行段可独立批准，全集未知仍不能判S2通过。
  - 历史首次建库可将上述requests传入 `plan_update(..., audited_segments=...)`形成统一冻结计划；不受 `maintenance.start` 或最近更新日限制，不把20年目标缩为维护窗口。

- [x] **步骤4：重跑新增测试，再仅验证共用扫描直接受影响的原例。**

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests/test_data_archive.py -q -k 'raw_archive or coverage_plan_keeps or missing_year_market_or_exit or unknown_security_attributes or official_no_trade or conflicting_prices or daily_suspension'
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests/test_research_data_delivery.py -q -k 'missing_independent_historical_target or archive_has_own_gates or factor_conflict_limits_archive'
```

预期新增测试通过；研究文件仅因共用目标／扫描接口变化执行直接回归，不启动策略。原完整状态、因子冲突和无交易证据仍可查询；不修改旧验收结论。
- [x] **步骤5：记录接口与结果，按需提交评审。** 更新DATA字段说明；未经另行提交授权不运行git commit。

## 4. 任务2：日额度与整批额度分开，沿原维护链接续

**文件：** 修改 `data/src/hetu_data/updates.py`、`data/src/hetu_data/worker.py`、`quant/DATA.md`；测试 `quant/tests/test_data_updates.py`，复用其中 `contracts/maintenance_case` 及 `quant/tests/source_cases.py` 的原始响应。

**接口：** 保留 `plan_update(request, store, contracts, *, audited_segments=None) -> dict`、`maintenance_plan(store, target_date, products, *, mode, contracts) -> dict`、`execute_update(plan, store, ledger: Path, *, cancelled, progress) -> dict`及 `approved_usage_ledger(root: Path, approved_scope_ref: str) -> Path`。

- 原 `limits` 中METERS数字继续表示整批批准上限；新增 `daily_limits: dict[str, int | float]`、`quota_timezone: str`、`quota_scope_ref: str`。quota_scope_ref是来源／账户共用额度的非凭据标识，daily_limits为该scope的源日上限，不能按批准范围各领一份；每日值来自已证来源合同。
- 可预留日额度键限 `provider_fetch/sdk_calls/upstream_product_requests/network_attempts/auth_calls/retries`；若批准合同设置字节硬限却无法计量，保留对应受阻，不假造0。未设置该硬限时披露字节估计的不确定性；rows与费用按原已知口径报告，不造零值或预留能力。
- 有daily_limits必须有已证时区；测试用 `Asia/Shanghai`。按实际请求时间计算额度日，不能按用户目标日、as_of、job_id重新划账；日额度恢复不提高整批批准总量。
- 同一 `approved_scope_ref`仍共用账本并锁定完整limits。事件增加 `quota_date/quota_timezone`；重开由事件恢复当日与累计用量，旧事件按原at解析，无日期证据不猜可用额度。
- 日额度在既有worker根锁内汇总当前根 `updates/approved-usage/` 已登记批准账本中同quota_scope_ref、同额度日的usage；两批次共享日额，但各自actual_usage仍独立累计。同scope的时区／日上限互相矛盾则拒绝执行，不新建计量服务。
- 该汇总不扫描未知根；旧账本缺scope或其他根／人工用量未纳入时，不猜账号剩余额度为满额。阶段04须用已证来源剩余额度给出可执行上限及适用范围，否则该域保持未知，不承诺全账号额度保护。
- 结果保留 `snapshot_ids/completed/pending/failures/actual_usage/incremental_usage`；增加 `daily_usage={date, timezone, quota_scope_ref, usage}`（scope共用日量）、`stop_reason`、`resume_after`和 `last_success_at`（最后completed事件的实际时间，未成功为None）。
- `stop_reason`取 `daily_limit/batch_limit/cancelled/failure/uncertain/None`；`resume_after`仅日限给出下一额度日起点。耗尽在请求边界停止，日限未发出的请求不记传输失败或消耗重试额度；worker沿原终态和显式恢复入口呈现可接续原因，不另加调度器。
- 日限／批次限触发后停止本次余下请求，stop_reason记录首次停止原因；失败重试仅沿原 `ConnectionError/TimeoutError/OSError` 或发送前取消的 `InterruptedError`，且累计不超过已批准retries。结构性缺失不重发。
- `maintenance_plan`原domains及15自然日、自然日事件、完整回看、退出对象、日历修订和跨根许可语义保持；新市场日历适用合同复用任务1，不重新建立当前存续池。
- 冻结计划增加 `library_ref: {library_id, root} | None`，明确已有库接续起点；首次无库可None。结果增加同形 `library_ref`，供页面和04直接打开本次库，不要求用户手拼成功片。

- [x] **步骤1：补两组受控时钟测试。** 使用现有 `SourceClient/response`，只替换传输和updates实际时钟，不替换计划、存储、任务或账本函数。
  - `test_daily_quota_resume_preserves_batch_usage`：两个分片，整批network_attempts=2、日限=1；第一日只发1次、第二片pending；同日换任务仍0新增，次日接续第2片且累计2，第三日新未完成片仍因批次2拒绝。
  - 断言 `daily_usage.usage.network_attempts` 随日恢复计数、`actual_usage.network_attempts`不清零；改approved limits仍被拒绝；已完成片不重发，旧快照可读。
  - 同测试另建一个approved_scope_ref但使用相同quota_scope_ref；两批次日限1时当日总发送仍只能1，第二批不能以新计划恢复源日额；次日接续仍分别受各自批次限额约束。
  - `test_daily_quota_inside_fetch_resumes_remaining_calls`：复用stock_basic的L/D/P多调用原形，L后日限停止；次日D/P接续，已落盘L不重发，取得时点保持，未发送边界不额外扣retry；模拟取消与结果未知仍分别遵守原恢复规则。
  - 第一组经真实 `run_maintenance_job` 驱动，用同组历史／增量fixture断言日限及取消后已成功片被纳入返回library_ref、原历史仍可查；次日继续后两片同库，重复显式恢复不重发也不重复发布相同片。

- [x] **步骤2：运行新增测试，确认日额度未实现的实际失败。**

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests/test_data_updates.py -q -k 'daily_quota'
```

- [x] **步骤3：在既有reserve／before回调增加双重核额。** 先验证批次与当日可用量再发送；保存部分响应和pending原因，日限作为可接续控制结果处理。下一次显式执行读取同一批准账本，不自动新建批准或重发未知传输。
  - worker按相同批准范围保留用量及未完成段，原件与发布仍用既有路径；预览展示batch/daily两种上限及已知计量，不触发获取。
  - **唯一库发布位置为 `data.worker.run_maintenance_job` 中execute_update返回之后、内容复核／取消终态之前。** 对成功snapshot_ids调用01的 `publish_library(store, asset_snapshots=..., previous=...)`；日限／取消／局部失败不丢已成功片。
  - previous取同一批准账本最后 `type='library_published'` 事件的库引用，否则取冻结library_ref；新事件记录 `library_id/root/snapshot_ids`。已纳入片全跳过，发布失败保留原库和成功快照，下次显式恢复可仅重试发布；不按日期或新任务重置引用。
  - 沿用现有已完成段、跨根授权、回看和日历修订实现；只在任务1日历选择改变的路径接入新合同。

- [x] **步骤4：运行新增测试与额度／取消直接回归。**

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests/test_data_updates.py -q -k 'daily_quota or existing_approval_limits or worker_budget_stop or multi_request_fetch_cancel or uncertain_attempt or successful_empty or failed_response_bytes'
```

预期全部通过，真实外部请求0。既有 `test_fifteen_calendar_days_include_weekend_events_and_exits`、`test_daily_maintenance_preserves_full_revision_window_and_exited_objects`、`test_bounded_calendar_refresh_fetches_and_consumes_revision` 的有效证据直接沿用；若任务1实际改变其消费路径，才对对应例增量回归。
- [x] **步骤5：向阶段03交付字段合同及样例。** 页面区分旧目标完成、目标截止、实际覆盖与最后成功时间；按需评审，提交另授权。

## 5. 任务3：获准库备份及清单恢复

**文件：** 修改 `data/src/hetu_data/archive.py`、`data/src/hetu_data/access.py`、必要的 `data/src/hetu_data/library.py`清单路径解析及 `quant/DATA.md`；测试 `quant/tests/test_data_archive.py`、必要的 `quant/tests/test_data_sources.py`许可断言。

**已有合同：** `backup_archive(store, ref, destination)`、`verify_archive_backup(destination) -> SnapshotRef`已保留manifest、raw、标准文件、引用evidence、配置及授权记录；旧生产布局与证据缺失测试继续有效，不重造复制器。

**新增接口：** `backup_library(store: DataStore, ref: LibraryRef, destination: Path) -> LibraryRef`；`verify_library_backup(destination: Path) -> LibraryRef`。复用01的库清单及既有单快照资产校验，保持旧单快照接口。

- 扩展现有 `check_product_access(..., operation=...)` 接受 `backup`；新库备份和旧 `backup_archive` 都检查backup，旧接口签名不变。付费授权须明确含backup，read不自动包含复制；免费产品按 `permission.allowed_operations` 的明确backup许可检查，不改变既有免费读取行为。
- 恢复验证只校验已在目标目录的备份；任何复制仍先过backup操作许可，恢复后查询继续过当时read与代码／期间许可。`retained_use=True`仅沿原read语义，不扩成到期后backup权。
- 库备份清单固定 `library_id/library_manifest/files/source_roots`；files仍是相对路径及sha256，source_roots记录原根到备份内子根的映射。保留每个owner的配置／授权／原件／证据及来源标识，不合并不同owner许可。
- 固定库清单中的资产定位映射到备份子根，恢复返回相同library_id下可解析的LibraryRef；不改原库，不要求原路径存在，不复制token、任务队列或未登记文件。
- 配置按DataStore重开必需的产品合同字段白名单生成非凭据投影，不整体复制store.config；token／password／secret／Authorization头、环境变量值及连接凭据不进入备份。授权记录仅保留产品、对象／期间、许可依据和允许操作等非凭据字段，不能借备份延长授权。
- 复制前验证所有依赖权限、必要容量与目标目录不存在；旧资产和旧发布不被失败覆盖。只校验已锁定文件是否被改动，哈希不用于跨运行过期判断。

- [x] **步骤1：补备份授权与恢复行为测试。** 复用coverage case和01两根历史／增量库，在临时目录创建获准备份，不读取真实资产。
  - `test_library_backup_permissions_and_manifest_restore`：仅read授权时拒绝且无复制；加明确backup后成功，禁用原根访问再由备份重开；跨历史与增量逐字段查询一致，raw、evidence和库清单均可解析。
  - 同测试撤销恢复根read，断言备份校验不绕过查询许可；过期但仅允许retained read的来源不能新增backup，仍可读其旧合法资产。
  - 在合成配置加入token与Authorization字段，断言备份配置／清单中无该值且真实DataStore仍可重开；token不得通过日志或返回值泄露。
  - `test_library_restore_rejects_missing_registered_asset`参数化删除library manifest、一个源snapshot manifest、raw、evidence之一；指出具体缺件且不返回可用引用，原库保持可读。

- [x] **步骤2：运行新增测试，确认缺少库备份／backup许可检查的失败。**

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests/test_data_archive.py -q -k 'library_backup or library_restore'
```

- [x] **步骤3：实现新增接口并复用许可、清单和复制逻辑。** 对每个登记owner核验产品／对象／期间；清单恢复必要元数据，不能用“复制了Parquet”替代重开验证，不增加全库治理框架。
- [x] **步骤4：运行新增测试和被复用复制逻辑的直接回归。**

```bash
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests/test_data_archive.py -q -k 'library_backup or library_restore or archive_restore or production_layout_range_and_evidence_restore'
quant/.venv/bin/python -m pytest -c quant/pyproject.toml quant/tests/test_data_sources.py -q -k 'paid_grant_is_product_and_operation_scoped'
```

预期新增测试通过；原备份消费者的合成免费产品或付费grant显式增加backup测试授权，不把新增权利默认写入所有grant构造器。输出是机制证据，获准生产备份重开仍由阶段04完成。
- [x] **步骤5：更新恢复手册并完成阶段评审。** 提交前另行确认授权并运行 `git diff --check`；检查本阶段与路线图相对链接，不为无关旧结果扩大测试。

## 6. 完成、停止与下一阶段

三个任务的新增行为及直接回归通过，接口交给阶段03／04，即可报告本阶段工程完成；不标S2真实全库或S3生产维护已通过。
成功任务片到库引用的接线由任务2的data worker单一发布位置负责；04直接消费返回library_ref及已存覆盖报告，不再人工拼接下载批次。
全集、代码段／市场期间证据、源端真实行限、原331日冲突适用性、固定目标实际缺口与获准备份恢复只能在阶段04形成生产证据，本阶段不得预判。
权限／来源能力不足时保留明确缺口及已可读资产；结构性缺失不进入无限重试，范围取舍交用户决定，不静默降为可得子集。
失败只修复对应接缝并重验受影响例；不能以日期、as_of、哈希、分支或新文档使旧验收失效。未获提交、取数或真实分析授权时不执行对应动作。
